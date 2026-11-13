"""MCP parallel execution, resizing and submission failures, without model calls."""
import asyncio
import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from src.mcp import service, store, tools


@pytest.fixture
def runtime(monkeypatch):
    assert service._active == 0
    cfg = store.Settings(max_concurrent=40, requests_per_minute=120)
    monkeypatch.setattr(store, 'config', lambda: cfg)
    monkeypatch.setattr(store, 'require_access', lambda *args: None)
    monkeypatch.setattr(store, 'audit_call', lambda *args: None)
    service._rates.clear()
    yield cfg
    assert service._active == 0
    if service._executor is not None:
        service._executor.shutdown(wait=True)
        service._executor = None
        service._executor_limit = 0
    service._rates.clear()


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.005)


def test_parallel_above_default_pool_capacity_preserves_identity(runtime, monkeypatch):
    identity = contextvars.ContextVar('test_mcp_identity')
    entered, thread_ids = set(), set()
    lock, release = threading.Lock(), threading.Event()

    def execute(token, name, args, check):
        assert identity.get() == token['user_id']
        with lock:
            entered.add(token['user_id'])
            thread_ids.add(threading.get_ident())
        assert release.wait(10)
        check()
        return {'user': identity.get()}

    monkeypatch.setattr(tools, 'execute', execute)

    async def scenario():
        # A saturated/small default pool must not serialize MCP calls.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        jobs = []
        for i in range(40):
            reset = identity.set(str(i))
            jobs.append(asyncio.create_task(service.call({'user_id': str(i)}, 'read', {})))
            identity.reset(reset)
        try:
            await until(lambda: len(entered) == 40)
            assert len(thread_ids) == 40 and service._active == 40
            with pytest.raises(HTTPException) as busy:
                await service.call({'user_id': 'other'}, 'read', {})
            assert busy.value.status_code == 429
        finally:
            release.set()
            results = await asyncio.gather(*jobs)
        assert [r['user'] for r in results] == [str(i) for i in range(40)]

    asyncio.run(scenario())


def test_live_grow_and_shrink_keep_running_work_counted(runtime, monkeypatch):
    runtime.max_concurrent = 2
    releases = [threading.Event() for _ in range(7)]
    entered = set()
    lock = threading.Lock()

    def execute(token, name, args, check):
        i = args['i']
        with lock:
            entered.add(i)
        assert releases[i].wait(10)
        check()
        return {'ok': True}

    monkeypatch.setattr(tools, 'execute', execute)

    async def scenario():
        jobs = []
        def start(i):
            task = asyncio.create_task(service.call({'user_id': str(i)}, 'read', {'i': i}))
            jobs.append(task)
            return task
        try:
            start(0)
            start(1)
            await until(lambda: len(entered) == 2)
            runtime.max_concurrent = 6
            for i in range(2, 6):
                start(i)
            await until(lambda: len(entered) == 6)
            runtime.max_concurrent = 2
            with pytest.raises(HTTPException) as busy:
                await service.call({'user_id': 'new'}, 'read', {'i': 6})
            assert busy.value.status_code == 429 and service._active == 6
            for i in range(5):
                releases[i].set()
            await asyncio.gather(*jobs[:5])
            assert service._active == 1
            new = start(6)
            await until(lambda: 6 in entered)
            assert service._active == 2
            releases[6].set()
            await new
        finally:
            for release in releases:
                release.set()
            await asyncio.gather(*jobs, return_exceptions=True)

    asyncio.run(scenario())


def test_submission_failure_releases_slot_and_allows_retry(runtime, monkeypatch):
    class BrokenPool:
        def submit(self, *args):
            raise RuntimeError('cannot start thread')

    monkeypatch.setattr(service, '_executor', BrokenPool())
    monkeypatch.setattr(service, '_executor_limit', runtime.max_concurrent)
    async def scenario():
        with pytest.raises(HTTPException) as failure:
            await service.call({'user_id': 'alice'}, 'read', {})
        assert failure.value.status_code == 503
        assert service._active == 0
        service._executor = None
        monkeypatch.setattr(tools, 'execute', lambda *args: {'ok': True})
        assert await service.call({'user_id': 'alice'}, 'read', {}) == {'ok': True}
    asyncio.run(scenario())


def test_submission_failure_after_enqueue_does_not_double_release(runtime, monkeypatch):
    class EnqueuedPool:
        queued = None
        def submit(self, *args):
            self.queued = args
            raise RuntimeError('thread creation failed after enqueue')

    pool = EnqueuedPool()
    monkeypatch.setattr(service, '_executor', pool)
    monkeypatch.setattr(service, '_executor_limit', runtime.max_concurrent)
    monkeypatch.setattr(tools, 'execute', lambda *args: pytest.fail('cancelled queued work must not execute'))

    async def scenario():
        try:
            with pytest.raises(HTTPException) as failure:
                await service.call({'user_id': 'alice'}, 'read', {})
            assert failure.value.status_code == 503 and service._active == 0
            # An existing executor thread may later drain the already queued item.
            fn, *args = pool.queued
            with pytest.raises(HTTPException) as cancelled:
                fn(*args)
            assert cancelled.value.status_code == 408
            assert service._active == 0
        finally:
            service._executor = None

    asyncio.run(scenario())
