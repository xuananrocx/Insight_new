"""Collect one complete streamed response inside the summary worker's thread."""
import asyncio
import time
import logging
import copy
from contextlib import aclosing

from src.core.api_retry import request_guard, request_deadline, failure_info
from src.core.summary_policy import policy, request_check
from src.core.llm_client import STREAM_TRUNCATED_MARK
from src.core.llm_providers import ChatText


async def _collect(client, messages, max_tokens, kwargs, deadline):
    # Legacy non-account providers are shared with chat. Give this worker private
    # async transports so asyncio.run never reuses a client from another loop.
    from src.core import accounts
    owned = []
    if not accounts.enabled and hasattr(client, '_providers'):
        client = copy.copy(client)
        client._providers = dict(client._providers)
        for name, wrapper in client._providers.items():
            wrapper = copy.copy(wrapper)
            if hasattr(wrapper, '_impl'):
                wrapper._impl = copy.copy(wrapper._impl)
                impl = wrapper._impl
            else:
                impl = wrapper
            impl._async_client = None
            client._providers[name] = wrapper
            owned.append(impl)
    async def consume():
        parts, provider, truncated, size = [], '', False, 0
        async with aclosing(client.chat_stream(messages, max_tokens=max_tokens,
                            api_retry_count=0, **kwargs)) as stream:
            async for text, name in stream:
                if name == STREAM_TRUNCATED_MARK:
                    truncated = True
                else:
                    size += len(text)
                    if size > 4_000_000:
                        raise ValueError('摘要响应异常过大，已停止接收')
                    parts.append(text)
                    provider = name or provider
        return ChatText(''.join(parts), stop_reason='max_tokens' if truncated else 'end_turn'), provider

    task = asyncio.create_task(consume())
    try:
        while True:
            request_guard.get()()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('摘要步骤达到总时长预算，已保留完成结果')
            done, _ = await asyncio.wait({task}, timeout=min(.5, remaining))
            if done:
                request_guard.get()()
                return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for impl in owned:
            transport = getattr(impl, '_async_client', None)
            if transport is not None:
                close = getattr(transport, 'aclose', None) or transport.close
                await close()


def collect(client, messages, *, max_tokens, **kwargs):
    p = policy()
    deadline = min(time.monotonic() + p.step_timeout_seconds,
                   request_deadline.get() or float('inf'))
    for attempt in range(p.retry_count + 1):
        request_guard.get()()
        request_check.get()()
        started = time.monotonic()
        try:
            result = asyncio.run(_collect(client, messages, max_tokens, kwargs, deadline))
            logging.getLogger(__name__).info('摘要流完成 attempt=%s elapsed_ms=%s max_tokens=%s text_chars=%s stop_reason=%s', attempt + 1, round((time.monotonic()-started)*1000), max_tokens, len(result[0]), result[0].stop_reason)
            return result
        except Exception as exc:
            retryable, _, status, wait = failure_info(exc)
            logging.getLogger(__name__).warning('摘要流失败 attempt=%s elapsed_ms=%s status=%s error_type=%s', attempt + 1, round((time.monotonic()-started)*1000), status, type(exc).__name__)
            if not retryable or attempt == p.retry_count or time.monotonic() >= deadline:
                raise
            if status in (429, 503) and wait > 30:
                raise
            until = min(deadline, time.monotonic() + max(wait, 2 ** attempt))
            while time.monotonic() < until:
                request_guard.get()()
                time.sleep(max(0, min(.25, until - time.monotonic())))
