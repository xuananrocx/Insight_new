"""Run separately with deploy/mcp dependencies: python -m tests.mcp_gateway_check."""
import asyncio
import json

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from src.mcp.gateway import create_app
from src.mcp import contracts


def definition():
    return [{'name': name, 'description': name, 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
             **({'outputSchema': contracts.OUTPUT_SCHEMA} if name in ('partial', 'busy') else {})}
            for name in ('list_knowledge_bases', 'wait', 'denied', 'partial', 'busy')]


async def run(backend_url):
    seen = []
    entered, cancelled = asyncio.Event(), asyncio.Event()
    valid = {'Bearer alice', 'Bearer bob'}
    backend_ready = True
    async def backend(request):
        if request.url.path == '/health':
            return httpx.Response(200 if backend_ready else 503, json={'status': 'ok' if backend_ready else 'unavailable'})
        assert str(request.url).startswith(backend_url + '/mcp-service/')
        auth = request.headers.get('authorization')
        seen.append((request.url.path, auth))
        if auth not in valid:
            return httpx.Response(401, json={'detail': 'invalid'})
        if request.url.path.endswith('/check'):
            return httpx.Response(200, json={'ok': True})
        if request.url.path.endswith('/tools'):
            return httpx.Response(200, json=definition())
        assert request.url.path.endswith('/call')
        payload = json.loads(request.content)
        if payload['name'] == 'wait':
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        if payload['name'] == 'partial':
            return httpx.Response(200, json=contracts.decorate({'error': 'evidence_budget', 'message': 'partial', 'evidence': [{'text': 'source'}]}, 'partial', {}))
        if payload['name'] == 'busy':
            return httpx.Response(429, json={'detail': contracts.error('busy', 'busy', legacy='429')})
        if payload['name'] == 'denied':
            return httpx.Response(403, json={'detail': 'revoked'})
        await asyncio.sleep(.01)
        return httpx.Response(200, json={'knowledge_bases': [{'name': auth}]})
    app = create_app(backend_url=backend_url, transport=httpx.MockTransport(backend))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:8001') as raw:
            health = await raw.get('/health')
            assert health.status_code == 200 and health.json()['component'] == 'insight-mcp'
            assert health.json()['version'] == __import__('src').__version__
            backend_ready = False
            assert (await raw.get('/health')).status_code == 503
            backend_ready = True
            assert (await raw.post('/mcp', json={})).status_code == 401
            assert (await raw.post('/mcp', headers={'Authorization': 'Bearer bad'}, json={})).status_code == 401
            headers = {'Authorization': 'Bearer alice', 'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json'}
            init = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}}
            assert (await raw.post('/mcp', headers={**headers, 'Host': 'evil.example'}, json=init)).status_code == 421
            assert (await raw.post('/mcp', headers={**headers, 'Origin': 'https://evil.example'}, json=init)).status_code == 403
            assert (await raw.post('/mcp', headers=headers, content=b'x'*16385)).status_code == 413
            initialized = await raw.post('/mcp', headers=headers, json=init)
            sid = initialized.headers['mcp-session-id']
            session_headers = {**headers, 'Mcp-Session-Id': sid, 'Mcp-Protocol-Version': '2025-06-18'}
            assert (await raw.post('/mcp', headers=[*session_headers.items(), ('Mcp-Session-Id', 'another-session')], json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})).status_code == 400
            assert (await raw.post('/mcp', headers={**session_headers, 'Authorization': 'Bearer bob'}, json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})).status_code == 404
            await raw.post('/mcp', headers=session_headers, json={'jsonrpc': '2.0', 'method': 'notifications/initialized'})
            waiting = asyncio.create_task(raw.post('/mcp', headers=session_headers, json={'jsonrpc': '2.0', 'id': 42, 'method': 'tools/call', 'params': {'name': 'wait', 'arguments': {}}}))
            await asyncio.wait_for(entered.wait(), 2)
            await raw.post('/mcp', headers=session_headers, json={'jsonrpc': '2.0', 'method': 'notifications/cancelled', 'params': {'requestId': 42, 'reason': 'user cancelled'}})
            await asyncio.wait_for(cancelled.wait(), 2)
            await asyncio.wait_for(waiting, 2)
            entered.clear()
            cancelled.clear()
            body = json.dumps({'jsonrpc': '2.0', 'id': 43, 'method': 'tools/call', 'params': {'name': 'wait', 'arguments': {}}}).encode()
            delivered, messages = False, []
            async def receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {'type': 'http.request', 'body': body, 'more_body': False}
                await entered.wait()
                return {'type': 'http.disconnect'}
            async def send(message):
                messages.append(message)
            scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'POST', 'scheme': 'http', 'path': '/mcp', 'raw_path': b'/mcp', 'query_string': b'', 'root_path': '', 'headers': [(key.lower().encode(), value.encode()) for key, value in {**session_headers, 'Host': '127.0.0.1:8001'}.items()], 'server': ('127.0.0.1', 8001), 'client': ('127.0.0.1', 12345)}
            await asyncio.wait_for(app(scope, receive, send), 3)
            assert cancelled.is_set()
            assert b'408' in b''.join(message.get('body', b'') for message in messages)
            valid.remove('Bearer alice')
            assert (await raw.post('/mcp', headers=session_headers, json={'jsonrpc': '2.0', 'id': 3, 'method': 'tools/list'})).status_code == 401
            valid.add('Bearer alice')
            assert (await raw.delete('/mcp', headers=session_headers)).status_code == 200
        async def client(secret):
            async with httpx.AsyncClient(transport=transport, headers={'Authorization': 'Bearer '+secret}) as http:
                async with streamable_http_client('http://127.0.0.1:8001/mcp', http_client=http) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        init = await session.initialize()
                        assert init.serverInfo.name == 'Insight Knowledge'
                        listed = await session.list_tools()
                        assert init.serverInfo.version == __import__('src').__version__
                        assert listed.tools[0].annotations.readOnlyHint
                        result = await session.call_tool('list_knowledge_bases', {})
                        assert not result.isError and result.structuredContent['knowledge_bases'][0]['name'] == 'Bearer '+secret
                        assert json.loads(result.content[0].text) == result.structuredContent
                        bad = await session.call_tool('list_knowledge_bases', {'extra': 1})
                        assert bad.isError
                        denied = await session.call_tool('denied', {})
                        assert denied.isError and denied.structuredContent['error'] == '403'
                        partial = await session.call_tool('partial', {})
                        assert not partial.isError and partial.structuredContent['outcome'] == 'partial'
                        busy = await session.call_tool('busy', {})
                        assert busy.isError and busy.structuredContent['code'] == 'busy'
                        assert busy.structuredContent['recovery']['delay_is_advisory']
        await asyncio.wait_for(asyncio.gather(client('alice'), client('bob')), 15)
    assert {auth for path, auth in seen if path.endswith('/call')} == {'Bearer alice', 'Bearer bob'}
    print('PASS: official SDK initialize/list/call, two concurrent identities, errors, cancellation/disconnect, session ownership, revocation, auth, Host/Origin, body limit')


if __name__ == '__main__':
    for invalid in ('ftp://backend.example', 'http://user:pass@backend.example', 'http://backend.example:bad',
                    'http://backend.example:65536', 'http://backend.example/path', 'http://backend.example?secret=x',
                    'http://backend.example#fragment', 'http://bad host', 'http://[broken'):
        try:
            create_app(backend_url=invalid)
        except ValueError:
            pass
        else:
            raise AssertionError('Accepted invalid backend origin: '+invalid)
    for origin in ('http://172.19.211.176:8888', 'https://backend.example'):
        asyncio.run(run(origin))
