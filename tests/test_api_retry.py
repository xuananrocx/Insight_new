import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest

from src.core import api_retry as retry, accounts, ai_call_logger
from src.core.llm_client import LLMClient
from src.core.llm_providers import OpenAIProvider, AnthropicProvider, AnthropicArchProvider, LLMError
from src.core.tool_chat import ToolChat, ToolHTTPError


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(accounts, 'enabled', False)
    monkeypatch.setattr(ai_call_logger, 'log_call', lambda **kw: None)
    monkeypatch.setattr(retry.random, 'uniform', lambda low, high: 1)


def client(provider):
    value = object.__new__(LLMClient)
    value._providers = {'test': provider}
    value._chat_chain = ['test']
    return value


def test_backoff_ten_retries_and_cap():
    values = [retry.retry_plan(httpx.ConnectError('offline'), i, 10, float('inf'))['delay'] for i in range(10)]
    assert values == [1, 2, 4, 8, 16, 30, 30, 30, 30, 30]
    assert retry.retry_plan(httpx.ConnectError('offline'), 10, 10, float('inf')) is None


@pytest.mark.parametrize('status', [500, 502, 504, 520, 522, 524, 599])
def test_gateway_retry_after_does_not_force_sixty_second_wait(status):
    assert retry.retry_plan(ToolHTTPError('bad gateway', status, 60), 0, 10, float('inf'))['delay'] == 1


@pytest.mark.parametrize('status', [429, 503])
def test_server_backpressure_is_respected_without_long_wait(status):
    assert retry.retry_plan(ToolHTTPError('busy', status, 20), 0, 10, float('inf'))['delay'] == 20
    with pytest.raises(retry.RetryDeferredError, match='60'):
        retry.retry_plan(ToolHTTPError('busy', status, 60), 0, 10, float('inf'))


def test_retry_header_formats_and_nonfinite_values():
    from email.utils import formatdate
    assert retry.retry_after({'retry-after-ms': '2500'}) == 2.5
    assert 18 < retry.retry_after({'retry-after': formatdate(time.time() + 20, usegmt=True)}) <= 20
    for value in ['bad', 'nan', 'inf', '-1']:
        assert retry.retry_after({'retry-after': value}) == 0


def test_invalid_protocol_wrapped_by_sdk_is_not_retried():
    from openai import APIConnectionError
    try:
        try:
            raise httpx.UnsupportedProtocol('invalid scheme')
        except httpx.UnsupportedProtocol as exc:
            raise APIConnectionError(request=httpx.Request('GET', 'https://example.test')) from exc
    except APIConnectionError as exc:
        assert retry.retry_plan(exc, 0, 10, float('inf')) is None


def test_api_embedding_uses_shared_retry_policy(monkeypatch):
    monkeypatch.setattr(retry.time, 'sleep', lambda _: None)
    calls = []
    def embed(texts):
        calls.append(texts)
        if len(calls) == 1:
            raise LLMError('embedding failed') from httpx.ConnectError('offline')
        return [[1.0]]
    value = client(SimpleNamespace(usable=True, embed=embed))
    value._embed_mode = 'api'
    value._embed_chain = ['test']
    assert value.embed(['text']) == ([[1.0]], 'test')
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_native_502_sixty_second_header_uses_short_retry(monkeypatch):
    requests, waits = [], []
    async def sleep(delay):
        waits.append(delay)
    monkeypatch.setattr(retry.asyncio, 'sleep', sleep)
    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(502, headers={'retry-after': '60'})
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'ready'}}]})
    p = OpenAIProvider('test', {'chat_model': 'test', 'base_url': 'https://example.test'}, 'fake')
    model = ToolChat(p, 'system', [], transport=httpx.MockTransport(handler))
    try:
        assert (await model.turn([])).text == 'ready'
        assert waits == [1] and len(requests) == 2
    finally:
        await model.close()


def test_sdk_retries_disabled_and_connection_timeout_separate():
    for cls in (OpenAIProvider, AnthropicProvider):
        p = cls('test', {'chat_model': 'test', 'request_timeout_seconds': 120}, 'fake')
        try:
            assert p.client.max_retries == 0 and p.test_client.max_retries == 0
            assert p.client.timeout.connect == 10
            assert p.client.timeout.read == 120
            assert p.test_client.timeout.read == 10
        finally:
            p.close()


def test_sync_sdk_path_has_exactly_one_retry_owner(monkeypatch):
    monkeypatch.setattr(retry.time, 'sleep', lambda _: None)
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(502, headers={'retry-after': '60'}, json={'error': {'message': 'bad gateway'}})
    from openai import OpenAI
    p = OpenAIProvider('test', {'chat_model': 'test', 'base_url': 'https://example.test'}, 'fake')
    p._client = OpenAI(api_key='fake', base_url=p.base_url, max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    try:
        with pytest.raises(LLMError):
            client(p).chat([{'role': 'user', 'content': 'q'}], api_retry_count=10)
        assert len(requests) == 11
    finally:
        p.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('permanent', [False, True])
async def test_enhanced_stream_retries_pre_output_without_sync_fallback(monkeypatch, permanent):
    original = asyncio.sleep
    async def no_wait(_):
        await original(0)
    monkeypatch.setattr(retry.asyncio, 'sleep', no_wait)
    from openai import AsyncOpenAI
    requests = []
    def handle(request):
        requests.append(request)
        if permanent or len(requests) == 1:
            return httpx.Response(401 if permanent else 502, json={'error': {'message': 'failed'}})
        return httpx.Response(200, text='data: {"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
    p = OpenAIProvider('test', {'chat_model': 'test', 'base_url': 'https://example.test'}, 'fake')
    p._async_client = AsyncOpenAI(api_key='fake', base_url=p.base_url, max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    p.chat = lambda *a, **kw: pytest.fail('stream must never fall back to synchronous request')
    try:
        if permanent:
            with pytest.raises(LLMError):
                _ = [part async for part in client(p).chat_stream([], api_retry_count=10)]
            assert len(requests) == 1
        else:
            assert [part async for part in client(p).chat_stream([], api_retry_count=10)] == [('ok', 'test')]
            assert len(requests) == 2
    finally:
        await p._async_client.close()


@pytest.mark.asyncio
async def test_no_restart_after_output_and_stream_cleanup():
    calls = []
    async def stream(timeout):
        calls.append(timeout)
        yield ('partial', 'test')
        raise httpx.ReadError('disconnected')
    result = []
    with pytest.raises(httpx.ReadError):
        async for value in retry.call_stream(stream, retry.RetryBudget.for_scene('qa_chat'), 'qa_chat'):
            result.append(value)
    assert len(calls) == 1 and result == [('partial', 'test')]


@pytest.mark.asyncio
async def test_anthropic_stream_uses_async_context_and_closes():
    closed = []
    class Stream:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            closed.append(True)
        @property
        def text_stream(self):
            async def chunks():
                yield 'ok'
            return chunks()
    p = AnthropicProvider('test', {'chat_model': 'test'}, 'fake')
    p._async_client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: Stream()))
    assert [text async for text in p.chat_stream([])] == ['ok']
    assert closed == [True]


def test_scene_budgets_and_sync_wall_timeout():
    assert retry.RetryBudget.for_scene('test').maximum == 1
    assert retry.RetryBudget.for_scene('title').maximum == 2
    assert retry.RetryBudget.for_scene('summary').maximum == 3
    assert retry.RetryBudget.for_scene('qa_chat').maximum == 10
    with pytest.raises(TimeoutError):
        retry.call_sync(lambda timeout: time.sleep(.1), retry.RetryBudget(10, time.monotonic() + .02), 'title')


@pytest.mark.asyncio
async def test_arch_http_status_preserved_and_no_fallback(monkeypatch):
    p = AnthropicArchProvider('test', {'chat_model': 'test', 'base_url': 'https://example.test'}, 'fake')
    p._async_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(502, headers={'retry-after': '60'})))
    p.chat = lambda *a, **kw: pytest.fail('no fallback')
    try:
        with pytest.raises(LLMError) as caught:
            _ = [text async for text in p.chat_stream([])]
        assert retry.failure_info(caught.value) == (True, 'HTTP 502', 502, 60)
    finally:
        await p._async_client.aclose()


@pytest.mark.parametrize('deferred', [False, True])
def test_connection_endpoint_uses_short_probe_and_reports_long_backpressure(monkeypatch, deferred):
    from fastapi import HTTPException
    from src.api import routes_accounts
    from src.core import account_llm
    called, closed = [], []
    def probe():
        called.append(True)
        if deferred:
            raise retry.RetryDeferredError('请等待 60 秒后重试')
        return True
    fake = SimpleNamespace(_providers={'test': SimpleNamespace(test_connection=probe)},
                           check_access=lambda: None, close=lambda: closed.append(True))
    monkeypatch.setattr(accounts, 'resolve_provider', lambda pid: {'name': 'test'})
    monkeypatch.setattr(accounts, 'bind_provider', lambda pid: None)
    monkeypatch.setattr(accounts, 'rate_limit', lambda *args: None)
    monkeypatch.setattr(accounts, 'user', lambda: {'id': 'test'})
    monkeypatch.setattr(account_llm, 'chat_client', lambda: fake)
    if deferred:
        with pytest.raises(HTTPException) as error:
            routes_accounts.test_provider('test')
        assert error.value.status_code == 503 and '60' in error.value.detail
    else:
        assert routes_accounts.test_provider('test')['ok'] is True
    assert called == [True] and closed == [True]


@pytest.mark.parametrize('status', range(400, 600))
def test_http_error_categories(status):
    expected = status >= 500 or status in (408, 429)
    assert retry.failure_info(ToolHTTPError('failure', status, 0))[0] is expected
