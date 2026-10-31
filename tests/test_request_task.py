import asyncio
import pytest
from src.core.request_task import until_disconnect


class Request:
    def __init__(self):
        self.closed = False

    async def receive(self):
        while not self.closed:
            await asyncio.sleep(0.01)
        return {'type': 'http.disconnect'}


@pytest.mark.asyncio
async def test_completion_returns_result():
    async def work():
        return {'supported': True}
    assert await until_disconnect(Request(), work()) == {'supported': True}


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['disconnect', 'timeout', 'parent_cancel'])
async def test_cancellation_waits_for_cleanup(reason):
    request = Request()
    started, cleaned = asyncio.Event(), asyncio.Event()
    async def work():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()
    task = asyncio.create_task(until_disconnect(request, work(), timeout=0.05 if reason == 'timeout' else 5))
    await started.wait()
    if reason == 'disconnect':
        request.closed = True
    elif reason == 'parent_cancel':
        task.cancel()
    with pytest.raises(TimeoutError if reason == 'timeout' else ConnectionAbortedError if reason == 'disconnect' else asyncio.CancelledError):
        await task
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_disconnect_does_not_cancel_other_request():
    first, second = Request(), Request()
    released = asyncio.Event()
    async def work():
        await released.wait()
        return 'ok'
    one = asyncio.create_task(until_disconnect(first, work()))
    two = asyncio.create_task(until_disconnect(second, work()))
    first.closed = True
    with pytest.raises(ConnectionAbortedError):
        await one
    assert not two.done()
    released.set()
    assert await two == 'ok'


@pytest.mark.asyncio
async def test_real_socket_disconnect_through_http_middleware():
    import socket
    import uvicorn
    from fastapi import FastAPI, Request as HTTPRequest, Response
    from starlette.middleware.base import BaseHTTPMiddleware

    app = FastAPI()
    started, cleaned = asyncio.Event(), asyncio.Event()
    class PassThrough(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            return await call_next(request)
    app.add_middleware(PassThrough)
    app.add_middleware(PassThrough)

    @app.post('/probe')
    async def probe(request: HTTPRequest):
        async def operation():
            try:
                started.set()
                await asyncio.sleep(60)
            finally:
                cleaned.set()
        try:
            await until_disconnect(request, operation())
        except ConnectionAbortedError:
            return Response(status_code=204)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off'))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
            _, writer = await asyncio.open_connection('127.0.0.1', port)
            writer.write(b'POST /probe HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n')
            await writer.drain()
            await started.wait()
            writer.close()
            await writer.wait_closed()
            await cleaned.wait()
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()
