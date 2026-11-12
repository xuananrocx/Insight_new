"""Read-only MCP isolation and real reading/navigation tests (no model/API calls)."""
import asyncio
import hashlib
import json
import threading
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.test_accounts_access import env, actor, kb  # noqa: F401 — shared account fixture
from src.core import accounts as a, permissions as p
from src.db import metadata_db as db
from src.knowledge import reading_index as ri
from src.mcp import store, tools, service
from src.mcp.routes import query_app
from src.qa.knowledge_tools import TOOLS, KnowledgeTools


@pytest.fixture
def setup(env):  # noqa: F811
    for table in ('mcp_references', 'mcp_calls', 'mcp_tokens'):
        a.execute(f'DELETE FROM {table}')
    db.set_state('mcp_settings', store.Settings(enabled=True, max_concurrent=2).model_dump())
    service._rates.clear()
    assert service._active == 0
    role = uuid.uuid4().hex
    a.execute('INSERT INTO permission_roles(id,name,permissions) VALUES (?,?,?)', (role, 'MCP test', json.dumps(['mcp.use'])))
    for uid in (env.alice_id, env.bob_id):
        a.execute('INSERT INTO permission_user_roles(user_id,role_id) VALUES (?,?)', (uid, role))
    env.kid = kb(env.alice, 'MCP private')
    env.other = kb(env.alice, 'Other')
    env.role = role
    yield env
    db.set_state('mcp_settings', {})
    a.execute('DELETE FROM permission_user_roles WHERE role_id=?', (role,))
    a.execute('DELETE FROM permission_roles WHERE id=?', (role,))
    service._rates.clear()


def token(env, client=None, kids=None):  # noqa: F811
    result = (client or env.alice).post('/api/v1/account/mcp/tokens', json={'name': 'Local Agent', 'kb_ids': kids or [env.kid], 'days': 7})
    assert result.status_code == 201, result.text
    return result.json()


def external(secret):
    return TestClient(query_app, headers={'Authorization': 'Bearer '+secret})


def invoke(client, name, **arguments):
    return client.post('/call', json={'name': name, 'arguments': arguments})


def test_default_closed_and_management_boundaries(setup):
    e = setup
    assert p.has('mcp.use', e.admin_id)  # existing super role is upgraded by permissions.init
    db.set_state('mcp_settings', {})
    assert e.alice.get('/api/v1/account/mcp').json()['enabled'] is False
    assert e.alice.post('/api/v1/account/mcp/tokens', json={'name': 'x', 'kb_ids': [e.kid]}).status_code == 409
    assert external('x').get('/check').status_code == 503
    assert e.alice.put('/api/v1/admin/mcp/settings', json={'enabled': True}).status_code == 403
    assert e.admin.put('/api/v1/admin/mcp/settings', headers={'X-CSRF-Token': 'bad'}, json={'enabled': True}).status_code == 403
    assert e.admin.put('/api/v1/admin/mcp/settings', json={'enabled': True, 'unknown': 1}).status_code == 422
    assert e.admin.put('/api/v1/admin/mcp/settings', json={'public_url': 'http://example.com/mcp'}).status_code == 422
    assert e.admin.put('/api/v1/admin/mcp/settings', json={'enabled': True, 'public_url': 'https://kb.example.com/mcp'}).status_code == 200
    assert e.admin.get('/api/v1/admin/mcp/settings').json()['enabled']
    t = token(e)
    bearer = TestClient(e.app, headers={'Authorization': 'Bearer '+t['token']})
    assert bearer.get('/api/v1/account/mcp').status_code == 401
    assert e.alice.get('/mcp-service/check').status_code == 401  # cookie cannot query
    assert bearer.get('/mcp-service/check').status_code == 200  # bearer works on mount
    assert bearer.get('/mcp-service/check', headers={'Origin': 'https://evil.test'}).status_code == 403


def test_tokens_hash_ownership_scope_and_live_revocation(setup):
    e = setup
    t = token(e)
    row = a.rows('SELECT * FROM mcp_tokens WHERE id=?', (t['item']['id'],))[0]
    assert row['token_hash'] == hashlib.sha256(t['token'].encode()).hexdigest()
    for client, endpoint in [(e.alice, '/api/v1/account/mcp'), (e.admin, '/api/v1/admin/mcp/tokens')]:
        response = client.get(endpoint)
        assert 'token_hash' not in response.text and t['token'] not in response.text
    assert e.bob.delete('/api/v1/account/mcp/tokens/'+row['id']).status_code == 404
    assert e.admin.delete('/api/v1/account/mcp/tokens/'+row['id']).status_code == 404
    assert e.bob.post('/api/v1/account/mcp/tokens', json={'name': 'steal', 'kb_ids': [e.kid]}).status_code == 403
    c = external(t['token'])
    assert [v['kb_id'] for v in invoke(c, 'list_knowledge_bases').json()['knowledge_bases']] == [e.kid]
    assert invoke(c, 'get_kb_overview', kb_id=e.other).status_code == 403
    assert e.alice.delete('/api/v1/account/mcp/tokens/'+row['id']).status_code == 200
    assert c.get('/check').status_code == 401
    assert e.admin.get('/api/v1/admin/mcp/calls').json()['total'] >= 1
    assert e.bob.get('/api/v1/admin/mcp/calls').status_code == 403


@pytest.mark.parametrize('change', ['expiry', 'disable', 'password', 'capability', 'global'])
def test_every_request_rechecks_current_access(setup, change):
    e = setup
    t = token(e)
    c = external(t['token'])
    assert c.get('/check').status_code == 200
    if change == 'expiry':
        a.execute('UPDATE mcp_tokens SET expires_at=0 WHERE id=?', (t['item']['id'],))
    elif change == 'disable':
        a.execute('UPDATE auth_users SET disabled=1 WHERE id=?', (e.alice_id,))
    elif change == 'password':
        a.execute('UPDATE auth_users SET must_change_password=1 WHERE id=?', (e.alice_id,))
    elif change == 'capability':
        a.execute('DELETE FROM permission_user_roles WHERE user_id=? AND role_id=?', (e.alice_id, e.role))
    else:
        db.set_state('mcp_settings', {})
    assert c.get('/check').status_code in (401, 403, 503)
    assert invoke(c, 'list_knowledge_bases').status_code in (401, 403, 503)


def test_kb_permission_removed_and_admin_not_a_bypass(setup):
    e = setup
    assert e.alice.put(f'/api/v1/kbs/{e.kid}/members/{e.bob_id}', json={'role': 'reader'}).status_code == 200
    c = external(token(e, e.bob)['token'])
    assert invoke(c, 'get_kb_overview', kb_id=e.kid).status_code == 200
    assert e.alice.delete(f'/api/v1/kbs/{e.kid}/members/{e.bob_id}').status_code == 200
    assert invoke(c, 'get_kb_overview', kb_id=e.kid).status_code == 403
    assert invoke(c, 'list_knowledge_bases').json()['knowledge_bases'] == []
    assert e.admin.post('/api/v1/account/mcp/tokens', json={'name': 'admin', 'kb_ids': [e.kid]}).status_code == 403


def test_advertised_schema_and_http_limits(setup, monkeypatch):
    e = setup
    c = external(token(e)['token'])
    defs = c.get('/tools').json()
    assert {t['name'] for t in defs} == {t['name'] for t in TOOLS} | {'list_knowledge_bases'}
    # MCP exposes canonical forms only; cannot bypass references with legacy arguments.
    for name, arguments in [('read_document', {'document_id': 1, 'version': 'x'}), ('get_document_outline', {'document_id': 1, 'object_id': 1}), ('search_knowledge', {'query': 'x', 'limit': 21}), ('search_knowledge', {'query': 'x', 'limit': True}), ('get_kb_overview', {'extra': 'x'})]:
        assert invoke(c, name, kb_id=e.kid, **arguments).status_code == 422
    assert invoke(c, 'delete_document', kb_id=e.kid).status_code == 400
    assert c.post('/call', content=b'x'*16385).status_code == 413
    assert c.post('/call', content=(b'x'*9000 for _ in range(2))).status_code == 413
    # No provider resolution, chat, or sessions are needed even if none is available.
    monkeypatch.setattr(a, 'resolve_provider', lambda *args, **kw: pytest.fail('MCP must not resolve chat provider'))
    count = a.rows('SELECT count(*) n FROM sessions')[0]['n']
    result = invoke(c, 'list_documents', kb_id=e.kid)
    assert result.status_code == 200 and 'documents' in result.json()
    assert a.rows('SELECT count(*) n FROM sessions')[0]['n'] == count


@pytest.fixture
def indexed(setup, tmp_path):
    path = tmp_path / 'types.h'
    path.write_text('struct Quote {\n'+''.join(f' int field_{i}; // unique keyword\n' for i in range(160))+' int visible_levels;\n};\n')
    fid = db.upsert_file(path.name, str(path), ri.fingerprint(path), path.stat().st_size, 'h', kb_id=setup.kid)
    db.set_file_processed(fid, [])
    file = db.get_file_by_path(path.name, setup.kid)
    builder = ri.build_file(file)
    with db.get_cursor() as cur:
        builder.publish(cur, fid, file['content_hash'])
    return file


def test_real_outline_read_scope_paging_and_stale_reference(setup, indexed, monkeypatch):
    e = setup
    monkeypatch.setattr(a, 'resolve_provider', lambda *args, **kw: pytest.fail('no chat API'))
    t = token(e, kids=[e.kid, e.other])
    c = external(t['token'])
    outline = invoke(c, 'get_document_outline', kb_id=e.kid, document_id=indexed['id'], query='Quote').json()
    assert 'error' not in outline, outline
    obj = outline['objects'][0]
    assert obj['read_ref'].startswith('mcp_') and obj['scope_ref'].startswith('mcp_')
    read = invoke(c, 'read_document', kb_id=e.kid, read_ref=obj['read_ref']).json()
    assert 'visible_levels' in read['evidence'][0]['text']
    # Each read is independent; no cumulative conversation evidence is retained.
    assert invoke(c, 'read_document', kb_id=e.kid, read_ref=obj['read_ref']).json()['evidence'][0]['text'] == read['evidence'][0]['text']
    members = invoke(c, 'get_object_members', kb_id=e.kid, scope_ref=obj['scope_ref']).json()
    assert members['next_offset'] is not None
    assert invoke(c, 'read_document', kb_id=e.other, read_ref=obj['read_ref']).status_code == 400
    other = external(token(e)['token'])
    assert invoke(other, 'read_document', kb_id=e.kid, read_ref=obj['read_ref']).status_code == 400
    result = invoke(c, 'search_scope', kb_id=e.kid, scope_ref=obj['scope_ref'], query='field_', match_mode='exact', limit=1).json()
    assert result.get('next_cursor'), result
    next_page = invoke(c, 'continue_search', kb_id=e.kid, cursor=result['next_cursor']).json()
    assert next_page['evidence'] != result['evidence']
    # Cross-invocation adapter equals the original reader's source text.
    with actor(e.alice_id):
        internal = tools.AccessTools(e.kid, store.authenticate(t['token']), lambda: None, 16000)
        original = internal.execute('get_document_outline', {'document_id': indexed['id'], 'query': 'Quote'})
        assert internal.execute('read_document', {'read_ref': original['objects'][0]['read_ref']})['evidence'][0]['text'] == read['evidence'][0]['text']
    builder = ri.build_file(indexed)
    with db.get_cursor() as cur:
        builder.publish(cur, indexed['id'], indexed['content_hash'])
    changed = invoke(c, 'read_document', kb_id=e.kid, read_ref=obj['read_ref']).json()
    assert changed['error'] == 'document_changed', changed
    assert invoke(c, 'continue_search', kb_id=e.kid, cursor=result['next_cursor']).json().get('error')
    a.execute('UPDATE mcp_references SET expires_at=0')
    assert invoke(c, 'read_document', kb_id=e.kid, read_ref=obj['read_ref']).status_code == 400


def test_output_bound_and_reference_cleanup(setup, monkeypatch):
    e = setup
    c = external(token(e)['token'])
    def huge(self, name, args):
        self.read_refs['r'] = {'x': 'sample'}
        return {'read_ref': 'r', 'text': 'x'*32000}
    monkeypatch.setattr(KnowledgeTools, 'execute', huge)
    assert invoke(c, 'get_kb_overview', kb_id=e.kid).status_code == 413
    assert not a.rows('SELECT * FROM mcp_references')
    def expanded(self, name, args):
        self.read_refs['r'] = {'x': 'sample'}
        result = {'read_ref': 'r', 'text': ''}
        result['text'] = 'x' * (32000 - len(json.dumps(result)) - 1)
        return result
    monkeypatch.setattr(KnowledgeTools, 'execute', expanded)
    # Fits before conversion, exceeds the limit after handle/envelope expansion.
    assert invoke(c, 'get_kb_overview', kb_id=e.kid).status_code == 413
    assert not a.rows('SELECT * FROM mcp_references')


def test_rate_shared_by_tokens(setup):
    e = setup
    cfg = store.config().model_copy(update={'requests_per_minute': 1})
    db.set_state('mcp_settings', cfg.model_dump())
    first, second = external(token(e)['token']), external(token(e)['token'])
    assert invoke(first, 'list_knowledge_bases').status_code == 200
    assert invoke(second, 'list_knowledge_bases').status_code == 429


def test_cancel_retains_concurrency_until_worker_finishes(setup, monkeypatch):
    e = setup
    t = token(e)
    entered, finish, exited = threading.Event(), threading.Event(), threading.Event()
    def block(token, name, args, check):
        entered.set()
        try:
            finish.wait(2)
            check()
            return {'ok': True}
        finally:
            exited.set()
    monkeypatch.setattr(tools, 'execute', block)
    cfg = store.config().model_copy(update={'max_concurrent': 1})
    monkeypatch.setattr(store, 'config', lambda: cfg)
    async def scenario():
        with actor(e.alice_id):
            row = store.authenticate(t['token'])
            task = asyncio.create_task(service.call(row, 'list_knowledge_bases', {}))
            await asyncio.to_thread(entered.wait, 1)
            assert entered.is_set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert service._active == 1
            with pytest.raises(HTTPException) as exc:
                await service.call(row, 'list_knowledge_bases', {})
            assert exc.value.status_code == 429
            finish.set()
            await asyncio.to_thread(exited.wait, 1)
            # Audit and slot release follow the worker body.
            for _ in range(50):
                if not service._active:
                    break
                await asyncio.sleep(.01)
            assert service._active == 0
    try:
        asyncio.run(scenario())
    finally:
        finish.set()


def test_deadline_and_revocation_during_worker(setup, monkeypatch):
    e = setup
    t = token(e)
    cfg = store.config().model_copy(update={'timeout_seconds': .03})
    original = store.config
    monkeypatch.setattr(store, 'config', lambda: cfg)
    def delayed(token, name, args, check):
        import time
        time.sleep(.06)
        check()
        return {'ok': True}
    monkeypatch.setattr(tools, 'execute', delayed)
    async def timed():
        with actor(e.alice_id):
            with pytest.raises(HTTPException) as exc:
                await service.call(store.authenticate(t['token']), 'list_knowledge_bases', {})
            assert exc.value.status_code == 408
            # Drain the timed-out underlying operation before ending the test loop.
            for _ in range(50):
                if not service._active:
                    break
                await asyncio.sleep(.01)
            assert service._active == 0
    asyncio.run(timed())
    monkeypatch.setattr(store, 'config', original)
    def revoked(token, name, args, check):
        a.execute('UPDATE mcp_tokens SET revoked_at=1 WHERE id=?', (token['id'],))
        return {'private': 'must not be returned'}
    monkeypatch.setattr(tools, 'execute', revoked)
    async def in_flight():
        with actor(e.alice_id):
            with pytest.raises(HTTPException) as exc:
                await service.call(store.authenticate(t['token']), 'list_knowledge_bases', {})
            assert exc.value.status_code == 401
    asyncio.run(in_flight())


def test_concurrent_token_creation_enforces_account_limit(setup):
    from concurrent.futures import ThreadPoolExecutor
    e = setup
    first = token(e)
    row = a.rows('SELECT * FROM mcp_tokens WHERE id=?', (first['item']['id'],))[0]
    # Leave one slot free and race two independent management requests.
    for _ in range(18):
        tid = uuid.uuid4().hex
        a.execute('INSERT INTO mcp_tokens(id,user_id,name,token_hash,prefix,kb_ids,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?)',
                  (tid, e.alice_id, 'fixture', tid, 'test', row['kb_ids'], row['created_at'], row['expires_at']))
    def create():
        return e.alice.post('/api/v1/account/mcp/tokens', json={'name': 'parallel', 'kb_ids': [e.kid]}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert sorted(results) == [201, 409]
    assert e.alice.delete('/api/v1/account/mcp/tokens/'+first['item']['id']).status_code == 200
    assert create() == 201  # failed transaction released its write lock
