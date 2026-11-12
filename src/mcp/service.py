"""Bounded MCP query execution. Timed-out workers retain their slot until exit."""
from __future__ import annotations

import asyncio
import contextvars
import threading
import time
from collections import defaultdict, deque
from fastapi import HTTPException
from src.mcp import store, tools

_lock = threading.Lock()
_active = 0
_rates = defaultdict(deque)


async def call(token, name, arguments, disconnected=None):
    global _active
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
            raise HTTPException(429, 'MCP 请求频率过高，请稍后重试')
        if _active >= cfg.max_concurrent:
            raise HTTPException(429, 'MCP 查询繁忙，请稍后重试')
        bucket.append(started)
        _active += 1
    cancelled = threading.Event()
    deadline = started + cfg.timeout_seconds

    def check():
        if cancelled.is_set() or time.monotonic() >= deadline:
            raise HTTPException(408, 'MCP 查询已取消或超时')

    def work():
        global _active
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
                with _lock:
                    _active -= 1

    ctx = contextvars.copy_context()
    # asyncio.to_thread copies context; explicit context keeps this invariant visible.
    worker = asyncio.create_task(asyncio.to_thread(ctx.run, work))
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
