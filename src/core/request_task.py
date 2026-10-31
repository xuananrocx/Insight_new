"""Run a short request-owned task; never leave it running after disconnect."""
import asyncio
from contextlib import suppress


async def until_disconnect(request, operation, *, timeout=90):
    stopped = False
    async def disconnected():
        while not stopped:
            message = await request.receive()
            if message['type'] == 'http.disconnect':
                return

    task = asyncio.create_task(operation)
    watcher = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait((task, watcher), timeout=timeout,
                                     return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return await task
        if watcher in done:
            await watcher
            raise ConnectionAbortedError('client disconnected')
        raise TimeoutError('test timed out')
    finally:
        stopped = True
        for pending in (task, watcher):
            if not pending.done():
                pending.cancel()
        for pending in (task, watcher):
            with suppress(asyncio.CancelledError, Exception):
                await pending
