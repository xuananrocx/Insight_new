"""Optional SDK gateway. Run in its own venv; never imports Insight storage/models."""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import time
import json
import os
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import anyio
import httpx
from mcp import types
from src import __version__
from src.mcp import contracts
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse

bearer = contextvars.ContextVar('mcp_bearer', default='')


def create_app(backend_url=None, transport=None):
    backend = (backend_url or os.environ.get('INSIGHT_BACKEND_URL', 'http://127.0.0.1:8000')).rstrip('/')
    try:
        parsed = urlsplit(backend)
        parsed.port
    except ValueError as exc:
        raise ValueError('INSIGHT_BACKEND_URL must be a plain HTTP(S) backend origin') from exc
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')
            or any(c.isspace() for c in backend)):
        raise ValueError('INSIGHT_BACKEND_URL must be a plain HTTP(S) backend origin')
    server = Server('Insight Knowledge', version=__version__, instructions=contracts.INSTRUCTIONS)
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=os.environ.get('INSIGHT_MCP_ALLOWED_HOSTS', '127.0.0.1:*,localhost:*,[::1]:*').split(','),
        allowed_origins=[s for s in os.environ.get('INSIGHT_MCP_ALLOWED_ORIGINS', '').split(',') if s],
    )
    manager = StreamableHTTPSessionManager(server, json_response=True,
                                          security_settings=security, max_request_body_size=16384,
                                          session_idle_timeout=300, max_sessions=128)
    # Protocol sessions enable native MCP cancellation, never store agent/chat state.
    # Bind their ownership separately: a valid token must not hijack another session.
    owners = {}
    client = None

    async def request(path, payload=None):
        return await client.request('GET' if payload is None else 'POST', backend+'/mcp-service/'+path,
                                    headers={'Authorization': 'Bearer '+bearer.get()}, json=payload)

    @server.list_tools()
    async def list_tools():
        response = await request('tools')
        response.raise_for_status()
        return [types.Tool(**tool, annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)) for tool in response.json()]

    async def forward_call(name, arguments):
        incoming = server.request_context.request
        pending = asyncio.create_task(request('call', {'name': name, 'arguments': arguments}))
        try:
            while not pending.done():
                if incoming is not None and await incoming.is_disconnected():
                    return httpx.Response(408, json={'detail': 'MCP 客户端已断开'})
                await asyncio.wait({pending}, timeout=.2)
            return pending.result()
        finally:
            if not pending.done():
                pending.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(pending, return_exceptions=True)

    @server.call_tool()
    async def call_tool(name, arguments):
        started = time.monotonic()
        try:
            response = await forward_call(name, arguments)
        except httpx.TimeoutException:
            value = contracts.error('timeout', '知识服务超时，请稍后重试')
        except httpx.RequestError:
            value = contracts.error('unavailable', '知识服务暂不可用')
        else:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if response.is_success and isinstance(payload, dict) and payload:
                value = payload
            else:
                detail = payload.get('detail', '知识服务请求失败') if isinstance(payload, dict) else None
                value = detail if isinstance(detail, dict) and detail.get('contract_version') else contracts.error(str(response.status_code) if not response.is_success else 'invalid_response', detail if isinstance(detail, str) else '知识服务响应异常')
        logging.getLogger(__name__).info('MCP gateway request=%s tool=%s backend_roundtrip_ms=%.3f outcome=%s',
            value.get('request_id'), name, (time.monotonic()-started)*1000, value.get('outcome'))
        return types.CallToolResult(isError=value.get('outcome') == 'error' if 'outcome' in value else bool(value.get('error')), structuredContent=value,
                                    content=[types.TextContent(type='text', text=json.dumps(value, ensure_ascii=False))])

    @asynccontextmanager
    async def lifespan(app):
        nonlocal client
        async with httpx.AsyncClient(timeout=135, trust_env=False, transport=transport,
                                    limits=httpx.Limits(max_connections=None, max_keepalive_connections=20)) as client:
            async with manager.run():
                yield

    class Endpoint:
        async def __call__(self, scope, receive, send):
            pairs = [(key.lower(), value) for key, value in scope.get('headers', [])]
            for sensitive in (b'authorization', b'mcp-session-id', b'host', b'origin'):
                if sum(key == sensitive for key, _ in pairs) > 1:
                    return await JSONResponse({'detail': '重复的安全请求头'}, 400)(scope, receive, send)
            headers = dict(pairs)
            auth = headers.get(b'authorization', b'').decode('latin1')
            if not auth.startswith('Bearer ') or not 1 <= len(auth[7:]) <= 200:
                return await JSONResponse({'detail': '需要 MCP 访问令牌'}, 401, headers={'WWW-Authenticate': 'Bearer', 'Cache-Control': 'no-store'})(scope, receive, send)
            binding = bearer.set(auth[7:])
            try:
                try:
                    response = await request('check')
                except httpx.RequestError:
                    return await JSONResponse({'detail': '知识服务暂不可用'}, 503)(scope, receive, send)
                if not response.is_success:
                    status = response.status_code if response.status_code in (401, 403, 503) else 503
                    return await JSONResponse({'detail': 'MCP 接入关闭、令牌失效或没有权限'}, status, headers={'Cache-Control': 'no-store'})(scope, receive, send)
                now = time.monotonic()
                for old, (_, touched) in list(owners.items()):
                    if touched < now - 600:
                        owners.pop(old, None)
                sid = headers.get(b'mcp-session-id', b'').decode('latin1')
                owner = hashlib.sha256(auth[7:].encode()).hexdigest()
                if sid:
                    if sid not in owners or owners[sid][0] != owner:
                        return await JSONResponse({'detail': 'MCP 连接已失效，请重新连接'}, 404)(scope, receive, send)
                    owners[sid] = (owner, now)
                elif len(owners) >= 256:
                    return await JSONResponse({'detail': 'MCP 连接繁忙，请稍后重试'}, 503)(scope, receive, send)
                async def bind_session(message):
                    if message['type'] == 'http.response.start':
                        message.setdefault('headers', []).append((b'cache-control', b'no-store'))
                        for key, value in message['headers']:
                            if key.lower() == b'mcp-session-id' and 200 <= message['status'] < 300:
                                owners[value.decode('latin1')] = (owner, time.monotonic())
                        if sid and (message['status'] == 404 or (scope['method'] == 'DELETE' and message['status'] < 300)):
                            owners.pop(sid, None)
                    await send(message)
                # We return JSON for tool calls and do not send unsolicited events.
                # MCP permits 405 for the optional standalone GET/SSE stream.
                if scope['method'] == 'GET':
                    return await JSONResponse({'detail': '请使用 MCP POST 请求'}, 405, headers={'Allow': 'POST, DELETE'})(scope, receive, send)
                await manager.handle_request(scope, receive, bind_session)
            finally:
                bearer.reset(binding)

    async def health(request):
        # Readiness is independent of the business switch and never needs a token.
        # Do not expose backend addresses, credentials or configuration here.
        ready = False
        try:
            response = await client.get(backend + '/health', timeout=3)
            ready = response.is_success and response.json().get('status') == 'ok'
        except (httpx.RequestError, ValueError, AttributeError):
            pass
        return JSONResponse({'status': 'ok' if ready else 'unavailable',
                             'component': 'insight-mcp', 'version': __version__},
                            status_code=200 if ready else 503, headers={'Cache-Control': 'no-store'})

    return Starlette(routes=[Route('/health', health), Route('/mcp', Endpoint(), methods=['GET', 'POST', 'DELETE'])], lifespan=lifespan)


def main():
    import uvicorn
    uvicorn.run(create_app(), host=os.environ.get('INSIGHT_MCP_BIND', '127.0.0.1'),
                port=int(os.environ.get('INSIGHT_MCP_PORT', '8001')), access_log=False)


if __name__ == '__main__':
    main()
