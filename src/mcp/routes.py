"""Cookie-authenticated management and a separate bearer-only query surface."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict, StringConstraints
from starlette.responses import JSONResponse
from src.core import accounts as a, permissions as p
from src.mcp import store, tools, service, contracts

router = APIRouter(prefix='/api/v1', tags=['mcp'])


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    kb_ids: list[Annotated[str, StringConstraints(min_length=1, max_length=100)]] = Field(min_length=1, max_length=100)
    days: int = Field(default=30, ge=1, le=365)


@router.get('/account/mcp')
def personal():
    uid = a.user()['id']
    cfg = store.config()
    items = a.rows('SELECT * FROM mcp_tokens WHERE user_id=? ORDER BY created_at DESC LIMIT 100', (uid,))
    allowed = p.has('mcp.use')
    kbs = []
    for kid in a.visible_kb_ids() if allowed else []:
        item = a.db.get_kb(kid)
        if item:
            kbs.append({'id': kid, 'name': item['name']})
    return {'enabled': cfg.enabled, 'can_use': allowed, 'public_url': cfg.public_url,
            'items': [store.token_view(r) for r in items], 'knowledge_bases': kbs}


@router.post('/account/mcp/tokens', status_code=201)
def create(req: TokenRequest):
    return store.create_token(req.name, req.kb_ids, req.days)


@router.delete('/account/mcp/tokens/{tid}')
def remove(tid: str):
    store.revoke(tid)
    return {'ok': True}


@router.get('/admin/mcp/settings')
def configuration():
    p.require('system.view')
    return store.config()


@router.put('/admin/mcp/settings')
def configuration_update(req: store.Settings):
    p.require('system.edit')
    return store.save_config(req)


@router.get('/admin/mcp/tokens')
def admin_tokens(offset: int = 0, limit: int = 20):
    p.require('system.view')
    offset, limit = max(0, offset), max(1, min(100, limit))
    rows = a.rows('SELECT t.*,u.username FROM mcp_tokens t LEFT JOIN auth_users u ON u.id=t.user_id ORDER BY t.created_at DESC LIMIT ? OFFSET ?', (limit, offset))
    return {'items': [store.token_view(r) | {'username': r['username']} for r in rows], 'total': a.rows('SELECT count(*) n FROM mcp_tokens')[0]['n']}


@router.delete('/admin/mcp/tokens/{tid}')
def admin_revoke(tid: str):
    p.require('system.edit')
    store.revoke(tid, admin=True)
    return {'ok': True}


@router.get('/admin/mcp/calls')
def calls(offset: int = 0, limit: int = 20):
    p.require('system.view')
    offset, limit = max(0, offset), max(1, min(100, limit))
    return {'items': a.rows('''SELECT c.*,u.username,t.name token_name FROM mcp_calls c
        LEFT JOIN auth_users u ON u.id=c.user_id LEFT JOIN mcp_tokens t ON t.id=c.token_id
        ORDER BY c.id DESC LIMIT ? OFFSET ?''', (limit, offset)), 'total': a.rows('SELECT count(*) n FROM mcp_calls')[0]['n']}


class BearerAccess:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        try:
            if request.headers.get('origin'):
                raise HTTPException(403, '此接口仅供 MCP 网关访问')
            auth = request.headers.get('authorization', '')
            if not auth.startswith('Bearer '):
                raise HTTPException(401, '需要 MCP 访问令牌')
            token = store.authenticate(auth[7:])
            if request.method == 'POST':
                # Bound streamed/chunked bodies, not only Content-Length.
                body = bytearray()
                async for part in request.stream():
                    body.extend(part)
                    if len(body) > 16384:
                        raise HTTPException(413, '请求体过大')
                sent = False
                async def replay():
                    nonlocal sent
                    if not sent:
                        sent = True
                        return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                    return await receive()
                downstream_receive = replay
            else:
                downstream_receive = receive
        except HTTPException as exc:
            return await JSONResponse({'detail': exc.detail}, exc.status_code, headers={'Cache-Control': 'no-store'})(scope, receive, send)
        bindings = [(a.identity, a.rows('SELECT * FROM auth_users WHERE id=?', (token['user_id'],))[0]),
                    (a.selected_provider, None), (a.active_kb, None), (a.provider_snapshot, None), (a.provider_binding_error, None)]
        resets = [(var, var.set(value)) for var, value in bindings]
        scope['mcp_token'] = token
        async def secure_send(message):
            if message['type'] == 'http.response.start':
                message.setdefault('headers', []).append((b'cache-control', b'no-store'))
            await send(message)
        try:
            await self.app(scope, downstream_receive, secure_send)
        finally:
            for var, old in reversed(resets):
                var.reset(old)


query_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
query_app.add_middleware(BearerAccess)


@query_app.get('/check')
def check(request: Request):
    store.require_access(request.scope['mcp_token'])
    return {'ok': True}


@query_app.get('/tools')
def tool_list(request: Request):
    store.require_access(request.scope['mcp_token'])
    return tools.definitions()


class CallRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)
    arguments: dict = Field(default_factory=dict)


@query_app.post('/call')
async def tool_call(req: CallRequest, request: Request):
    binding = contracts.request_identity.set(contracts.request_id())
    try:
        return await service.call(request.scope['mcp_token'], req.name, req.arguments, request.is_disconnected)
    except HTTPException as exc:
        if isinstance(exc.detail, dict):
            raise
        raise HTTPException(exc.status_code, contracts.error(str(exc.status_code), str(exc.detail))) from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, contracts.error('422', '工具参数不正确，请按工具定义调用')) from exc
    finally:
        contracts.request_identity.reset(binding)
