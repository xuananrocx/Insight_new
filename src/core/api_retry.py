"""One retry policy for native tools and SDK-based model requests."""
from __future__ import annotations

import asyncio
import contextvars
import logging
import math
import random
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import httpx

logger = logging.getLogger(__name__)
retry_observer = contextvars.ContextVar("api_retry_observer", default=None)


def network_timeout(read=60):
    return httpx.Timeout(read, connect=min(10, read), write=min(30, read), pool=min(10, read))


def retry_after(headers):
    if not headers:
        return 0.0
    for name, scale in (("retry-after-ms", .001), ("retry-after", 1)):
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            value = float(raw) * scale
        except (ValueError, TypeError):
            try:
                value = parsedate_to_datetime(raw).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                continue
        if math.isfinite(value):
            return max(0, value)
    return 0.0


class RetryDeferredError(Exception):
    """The service asks us to wait longer than an interactive request permits."""


def failure_info(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        response = getattr(exc, "response", None)
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None) or getattr(response, "status_code", None)
        if isinstance(status, int):
            wait = getattr(exc, "retry_after", None)
            if wait is None:
                wait = retry_after(getattr(response, "headers", None))
            return (500 <= status < 600 or status in (408, 429)), f"HTTP {status}", status, wait
        if isinstance(exc, (httpx.UnsupportedProtocol, httpx.LocalProtocolError)):
            return False, type(exc).__name__, None, 0
        if isinstance(exc, (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ProxyError, TimeoutError)):
            return True, type(exc).__name__, None, 0
        if type(exc).__name__ in ("APIConnectionError", "APITimeoutError") and exc.__cause__ is None:
            return True, type(exc).__name__, None, 0
        exc = exc.__cause__ or exc.__context__
    return False, "不可重试错误", None, 0


def retry_plan(exc, attempt, maximum, deadline):
    retryable, reason, status, server_wait = failure_info(exc)
    if not retryable or attempt >= maximum:
        return None
    if status in (429, 503) and server_wait > 30:
        logger.info("API retry deferred status=%s server_wait_seconds=%s", status, server_wait)
        raise RetryDeferredError(f"服务暂时限流或不可用（HTTP {status}），建议等待 {math.ceil(server_wait)} 秒后重试；本次不自动长时间等待。") from exc
    base = min(2 ** min(attempt, 5), 30)
    delay = min(30, base * random.uniform(.9, 1.1))
    if status in (429, 503):
        delay = max(delay, server_wait)
    if time.monotonic() + delay + 1 >= deadline:
        return None
    return dict(attempt=attempt + 1, maximum=maximum, delay=delay, reason=reason,
                server_wait_seconds=server_wait, wait_source="server" if status in (429, 503) and server_wait >= delay else "backoff")


def report_retry(info, scene, meta=None, callback=None, request_ms=None):
    from src.core.llm_diagnostics import redact
    logger.info("API retry scene=%s session=%s turn=%s attempt=%s/%s reason=%s request_ms=%s delay_seconds=%.3f server_wait_seconds=%s wait_source=%s",
                scene, (meta or {}).get("session_id"), (meta or {}).get("turn_id"), info["attempt"], info["maximum"],
                redact(info["reason"]), request_ms, info["delay"], info["server_wait_seconds"], info["wait_source"])
    observer = callback or retry_observer.get()
    if observer:
        observer(**info)


@dataclass
class RetryBudget:
    maximum: int
    deadline: float

    @classmethod
    def for_scene(cls, scene, count=None):
        seconds, default = (20, 1) if scene == "test" else (30, 2) if scene == "title" else (90, 10) if scene == "conversation_summary" else (180, 10) if scene == "qa_chat" else (300, 3)
        return cls(min(10, max(0, default if count is None else int(count))), time.monotonic() + seconds)


def call_sync(call, budget, scene, meta=None, check=lambda: None):
    import concurrent.futures
    for attempt in range(budget.maximum + 1):
        check()
        remaining = budget.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("模型请求超过本次时间预算")
        started = time.monotonic()
        try:
            # Bound wall time as well as per-network-operation idle time.
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="api-request")
            context = contextvars.copy_context()
            future = executor.submit(context.run, call, network_timeout(min(60, remaining)))
            try:
                return future.result(timeout=remaining)
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
        except Exception as exc:
            plan = retry_plan(exc, attempt, budget.maximum, budget.deadline)
            if plan is None:
                raise
            check()
            report_retry(plan, scene, meta, request_ms=round((time.monotonic() - started) * 1000))
            time.sleep(plan["delay"])


async def call_stream(call, budget, scene, meta=None, check=lambda: None):
    from contextlib import aclosing
    for attempt in range(budget.maximum + 1):
        await asyncio.to_thread(check)
        emitted = False
        started = time.monotonic()
        try:
            async with asyncio.timeout(max(0, budget.deadline - time.monotonic())):
                async with aclosing(call(network_timeout(min(60, max(.01, budget.deadline - time.monotonic()))))) as stream:
                    async for item in stream:
                        emitted = True
                        yield item
            return
        except Exception as exc:
            if emitted:
                raise
            plan = retry_plan(exc, attempt, budget.maximum, budget.deadline)
            if plan is None:
                raise
            await asyncio.to_thread(check)
            report_retry(plan, scene, meta, request_ms=round((time.monotonic() - started) * 1000))
            await asyncio.sleep(plan["delay"])
