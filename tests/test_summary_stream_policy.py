"""Summary transport failures must never publish or consume unbounded requests."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError
from src.core.summary_policy import SummaryPolicy, active_policy, request_check, reduce_checkpoint
from src.core.summary_stream import collect
from src.core.api_retry import request_guard
from src.core.ingest_state import TaskCancelledError
from src.knowledge import ai_summarizer as summary


def test_policy_rejects_inconsistent_budgets():
    with pytest.raises(ValidationError):
        SummaryPolicy(output_tokens=64000, output_token_limit=32000)
    with pytest.raises(ValidationError):
        SummaryPolicy(step_timeout_seconds=500, task_timeout_seconds=100)
    with pytest.raises(ValidationError):
        SummaryPolicy(request_limit=0)


def test_partial_retry_discards_previous_text():
    calls, closed = [], []
    async def stream(*a, **kw):
        calls.append(kw)
        try:
            if len(calls) == 1:
                yield 'discard this', 'fake'
                raise httpx.ReadError('interrupted')
            yield 'complete answer', 'fake'
        finally:
            closed.append(True)
    counter = []
    t = request_check.set(lambda: counter.append(True))
    try:
        answer, _ = collect(SimpleNamespace(chat_stream=stream), [], max_tokens=1000, scene='summarize')
        assert answer == 'complete answer'
        assert len(counter) == len(closed) == 2
    finally:
        request_check.reset(t)


def test_cancel_while_stream_is_silent_closes_connection():
    closed, ticks = [], []
    async def stream(*a, **kw):
        try:
            await asyncio.sleep(20)
            yield 'must not return', 'fake'
        finally:
            closed.append(True)
    def guard():
        ticks.append(True)
        if len(ticks) >= 3:
            raise TaskCancelledError()
    token = request_guard.set(guard)
    try:
        with pytest.raises(TaskCancelledError):
            collect(SimpleNamespace(chat_stream=stream), [], max_tokens=1000, scene='summarize')
        assert closed == [True]
    finally:
        request_guard.reset(token)


def test_truncation_at_cap_never_retries_with_smaller_budget():
    from src.core.llm_client import STREAM_TRUNCATED_MARK
    calls = []
    async def stream(*a, **kw):
        calls.append(kw['max_tokens'])
        yield 'partial', 'fake'
        yield '', STREAM_TRUNCATED_MARK
    token = active_policy.set(SummaryPolicy(output_tokens=16000, output_token_limit=16000))
    try:
        with pytest.raises(ValueError, match='额度上限'):
            summary._checked_chat(SimpleNamespace(chat_stream=stream), [], scene='summarize')
        assert calls == [16000]
    finally:
        active_policy.reset(token)


def test_reduce_checkpoints_survive_failure(monkeypatch):
    cache, calls = {}, []
    def checkpoint(key, value):
        if value is None:
            return cache.get(key)
        cache[key] = value
    def chat(*args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise ValueError('fail second group')
        return '已完成的汇总' * 10, 'fake'
    monkeypatch.setattr(summary, '_checked_chat', chat)
    token = reduce_checkpoint.set(checkpoint)
    policy_token = active_policy.set(SummaryPolicy(reduce_chars=4000))
    notes = [('a', 'x' * 3000), ('b', 'y' * 3000)]
    try:
        with pytest.raises(ValueError, match='second group'):
            summary._reduce_notes(None, notes, 'kb', lambda: None, lambda _: None)
        assert len(cache) == 1
        result = summary._reduce_notes(None, notes, 'kb', lambda: None, lambda _: None)
        assert len(calls) == 3 and len(result) == 2
    finally:
        reduce_checkpoint.reset(token)
        active_policy.reset(policy_token)


@pytest.mark.asyncio
async def test_arch_stream_missing_stop_is_not_success():
    from src.core.llm_providers import AnthropicArchProvider, LLMError
    class Response:
        status_code = 200
        async def aiter_lines(self):
            yield 'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"partial"}}'
            yield 'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}'
    class Context:
        async def __aenter__(self):
            return Response()
        async def __aexit__(self, *a):
            pass
    p = AnthropicArchProvider('test', {'chat_model': 'fake'}, 'fake')
    p._async_client = SimpleNamespace(stream=lambda *a, **kw: Context())
    with pytest.raises(LLMError, match='message_stop'):
        _ = [v async for v in p.chat_stream([], status={'strict_summary': True})]


def test_gateway_timeout_splits_only_failed_group(monkeypatch):
    calls = []
    def chat(client, messages, **kwargs):
        calls.append(messages[1]['content'])
        if len(calls) == 1:
            response = httpx.Response(504, request=httpx.Request('POST', 'https://example.invalid'))
            raise httpx.HTTPStatusError('gateway timeout', request=response.request, response=response)
        return '完成汇总' * 20, 'fake'
    monkeypatch.setattr(summary, '_checked_chat', chat)
    token = active_policy.set(SummaryPolicy(reduce_chars=4000, split_depth=1))
    try:
        result = summary._reduce_notes(None, [('a', 'x'*3000), ('b', 'y'*3000)], 'kb', lambda: None, lambda _: None)
        assert len(calls) == 3
        assert len(result) == 2
        assert calls[1].count('x') + result[0][1].count('x') == 3000
        assert calls[2].count('y') == 3000
    finally:
        active_policy.reset(token)
