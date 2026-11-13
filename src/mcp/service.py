"""Bounded MCP query execution. Timed-out workers retain their slot until exit."""
from __future__ import annotations

import asyncio
import contextvars
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from fastapi import HTTPException
from src.mcp import store, tools, contracts

_lock = threading.Lock()
_active = 0
_rates = defaultdict(deque)
_executor: ThreadPoolExecutor | None = None
_executor_limit = 0


async def call(token, name, arguments, disconnected=None):
    global _active, _executor, _executor_limit
    cfg = store.config()
    store.require_access(token)
    started = time.monotonic()
    with _lock:
        # Buckets are per account, so creating extra tokens cannot multiply limits.
        for uid in list(_rates):
            if not _rates[uid] or _rates[uid][-1] < started-60:
                del _rates[uid]
        bucket = _rates[token['user_id']]
        while bucket and bucket[0] <= started-60:
            bucket.popleft()
        if len(bucket) >= cfg.requests_per_minute:
            raise HTTPException(429, contracts.error('rate_limited', 'MCP 请求频率过高，请稍后重试', legacy='429'))
        if _active >= cfg.max_concurrent:
            raise HTTPException(429, contracts.error('busy', 'MCP 查询繁忙，请稍后重试', legacy='429'))
        bucket.append(started)
        _active += 1
    cancelled = threading.Event()
    work_started = False
    released = False
    deadline = started + cfg.timeout_seconds

    def release():
        nonlocal released
        global _active
        with _lock:
            if not released:
                _active -= 1
                released = True

    def check():
        if cancelled.is_set() or time.monotonic() >= deadline:
            raise HTTPException(408, 'MCP 查询已取消或超时')

    def work():
        nonlocal work_started
        with _lock:
            work_started = True
        status = 'failed'
        try:
            check()
            result = tools.execute(token, name, arguments, check)
            check()
            status = 'error' if result.get('error') else 'ok'
            return result
        except HTTPException as exc:
            status = str(exc.status_code)
            raise
        finally:
            try:
                store.audit_call(token, name, arguments.get('kb_id'), status, started)
            finally:
                release()

    ctx = contextvars.copy_context()
    # Use an independent, lazily populated pool, not asyncio's shared fixed-size
    # executor. Admission counts include old pools' in-flight work during resizing.
    try:
        with _lock:
            if _executor is None or _executor_limit != cfg.max_concurrent:
                replacement = ThreadPoolExecutor(max_workers=cfg.max_concurrent, thread_name_prefix='mcp-query')
                previous, _executor = _executor, replacement
                _executor_limit = cfg.max_concurrent
                if previous is not None:
                    previous.shutdown(wait=False)
            future = _executor.submit(ctx.run, work)
    except Exception as exc:
        # submit() can fail after enqueueing if an OS thread cannot be created.
        # Cancel any queued work, but keep a running worker's slot until exit.
        with _lock:
            cancelled.set()
            if not work_started and not released:
                _active -= 1
                released = True
        raise HTTPException(503, contracts.error('unavailable', 'MCP 查询暂时无法启动，请稍后重试')) from exc
    worker = asyncio.wrap_future(future)
    def consume(task):
        if not task.cancelled():
            task.exception()
    worker.add_done_callback(consume)
    try:
        while not worker.done():
            if disconnected is not None and await disconnected():
                raise HTTPException(408, 'MCP 客户端已断开')
            left = deadline-time.monotonic()
            if left <= 0:
                raise HTTPException(408, 'MCP 查询超时，请缩小范围或稍后重试')
            await asyncio.wait({worker}, timeout=min(left, .2))
        result = worker.result()
        store.require_access(token, arguments.get('kb_id'))
        return result
    finally:
        cancelled.set()
