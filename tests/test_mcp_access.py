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
    db.set_state('mcp_retrieval_settings', {})
    for table in ('mcp_references', 'mcp_calls', 'mcp_tokens'):
        a.execute(f'DELETE FROM {table}')
    db.set_state('mcp_settings', store.Settings(enabled=True, max_concurrent=2, rerank_enabled=False).model_dump())
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
    db.set_state('mcp_retrieval_settings', {})
    a.execute('DELETE FROM permission_user_roles WHERE role_id=?', (role,))
    a.execute('DELETE FROM permission_roles WHERE id=?', (role,))
    service._rates.clear()


def token(env, client=None, kids=None, days=7):  # noqa: F811
    result = (client or env.alice).post('/api/v1/account/mcp/tokens', json={'name': 'Local Agent', 'kb_ids': kids or [env.kid], 'days': days})
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
    assert e.admin.put('/api/v1/admin/mcp/settings', json={'public_url': 'http://example.com/mcp'}).status_code == 200
    assert e.admin.put('/api/v1/admin/mcp/settings', json={'enabled': True, 'public_url': 'https://kb.example.com/mcp'}).status_code == 200
    assert e.admin.get('/api/v1/admin/mcp/settings').json()['enabled']
    t = token(e)
    bearer = TestClient(e.app, headers={'Authorization': 'Bearer '+t['token']})
    assert bearer.get('/api/v1/account/mcp').status_code == 401
    assert e.alice.get('/mcp-service/check').status_code == 401  # cookie cannot query
    assert bearer.get('/mcp-service/check').status_code == 200  # bearer works on mount
    assert bearer.get('/mcp-service/check', headers={'Origin': 'https://evil.test'}).status_code == 403


def test_deployment_status_requires_system_view(setup, monkeypatch):
    monkeypatch.delenv('INSIGHT_MCP_HEALTH_URL', raising=False)
    assert setup.alice.get('/api/v1/admin/mcp/status').status_code == 403
    assert setup.admin.get('/api/v1/admin/mcp/status').json()['state'] == 'not_configured'


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


def test_permanent_token_survives_time_but_not_revocation(setup, monkeypatch):
    e = setup
    permanent = token(e, days=None)
    finite = token(e, days=365)
    item = permanent['item']
    assert item['expires_at'] is None
    row = a.rows('SELECT * FROM mcp_tokens WHERE id=?', (item['id'],))[0]
    assert row['expires_at'] == store.PERMANENT_TOKEN_EXPIRY
    assert row['token_hash'] == hashlib.sha256(permanent['token'].encode()).hexdigest()
    for client, endpoint in [(e.alice, '/api/v1/account/mcp'), (e.admin, '/api/v1/admin/mcp/tokens')]:
        response = client.get(endpoint)
        saved = next(t for t in response.json()['items'] if t['id'] == item['id'])
        assert saved['expires_at'] is None
        assert 'token_hash' not in response.text and permanent['token'] not in response.text
    c = external(permanent['token'])
    later = row['created_at'] + 366 * 86400
    with monkeypatch.context() as patch:
        patch.setattr(store.time, 'time', lambda: later)
        assert external(finite['token']).get('/check').status_code == 401
        assert c.get('/check').status_code == 200
        assert [v['kb_id'] for v in invoke(c, 'list_knowledge_bases').json()['knowledge_bases']] == [e.kid]
    assert e.alice.delete('/api/v1/account/mcp/tokens/'+item['id']).status_code == 200
    assert c.get('/check').status_code == 401
    assert invoke(c, 'list_knowledge_bases').status_code == 401


def test_token_duration_defaults_bounds_and_strict_validation(setup):
    e = setup
    for duration in ({}, {'days': 1}, {'days': 365}):
        response = e.alice.post('/api/v1/account/mcp/tokens', json={'name': 'duration', 'kb_ids': [e.kid], **duration})
        assert response.status_code == 201, response.text
        item = response.json()['item']
        assert item['expires_at'] - item['created_at'] == duration.get('days', 30) * 86400
    for days in (0, -1, 366, True, 1.5, '30'):
        assert e.alice.post('/api/v1/account/mcp/tokens', json={'name': 'invalid', 'kb_ids': [e.kid], 'days': days}).status_code == 422


@pytest.mark.parametrize('url', ['http://172.19.211.176:8001/mcp', 'http://kb.example.com/mcp', 'https://kb.example.com/mcp', 'http://[fd00::1]:8001/mcp'])
def test_remote_protocol_and_custom_concurrency_round_trip(setup, url):
    response = setup.admin.put('/api/v1/admin/mcp/settings', json={'public_url': url, 'max_concurrent': 257})
    assert response.status_code == 200, response.text
    saved = setup.admin.get('/api/v1/admin/mcp/settings').json()
    assert saved['public_url'] == url and saved['max_concurrent'] == 257
    assert setup.alice.get('/api/v1/account/mcp').json()['public_url'] == url


@pytest.mark.parametrize('value', [0, -1, 1.5, True, '8'])
def test_concurrency_requires_positive_integer(setup, value):
    assert setup.admin.put('/api/v1/admin/mcp/settings', json={'max_concurrent': value}).status_code == 422


@pytest.mark.parametrize('url', ['ftp://kb.example.com/mcp', 'http://user:pass@kb.example.com/mcp',
                               'http://kb.example.com:bad/mcp', 'http://kb.example.com:65536/mcp',
                               'http://kb.example.com/mcp?q=x', 'http://kb.example.com/mcp#fragment',
                               'http://bad host/mcp', 'http://[broken'])
def test_remote_url_format_checks_remain(setup, url):
    assert setup.admin.put('/api/v1/admin/mcp/settings', json={'public_url': url}).status_code == 422


def test_call_user_filter_matches_all_pages_counts_and_literal_text(setup):
    e = setup
    a.execute('UPDATE permission_profiles SET display_name=? WHERE user_id=?', ('MCP Alice测试_%', e.alice_id))
    a.execute('UPDATE permission_profiles SET display_name=? WHERE user_id=?', ('MCP Bob', e.bob_id))
    alice_name = a.rows('SELECT username FROM auth_users WHERE id=?', (e.alice_id,))[0]['username']
    for uid in (e.alice_id, e.bob_id, e.alice_id, e.bob_id, e.alice_id, e.alice_id, 'deleted-user'):
        a.execute('''INSERT INTO mcp_calls(user_id,token_id,tool,status,duration_ms,created_at)
            VALUES (?,?,'list_documents','ok',12,1)''', (uid, 'deleted-token'))
    endpoint = '/api/v1/admin/mcp/calls'
    assert e.admin.get(endpoint).json()['total'] == 7
    assert e.admin.get(endpoint, params={'search': '  '}).json()['total'] == 7
    first = e.admin.get(endpoint, params={'search': alice_name.upper(), 'limit': 2}).json()
    second = e.admin.get(endpoint, params={'search': alice_name, 'limit': 2, 'offset': 2}).json()
    assert first['total'] == second['total'] == 4
    assert len(first['items']) == len(second['items']) == 2
    assert len({row['id'] for row in first['items'] + second['items']}) == 4
    assert all(row['user_id'] == e.alice_id and row['display_name'] == 'MCP Alice测试_%'
               for row in first['items'] + second['items'])
    assert first['items'][-1]['id'] > second['items'][0]['id']
    for search in ('Alice测试', '%', '_', '  Alice测试  '):
        result = e.admin.get(endpoint, params={'search': search}).json()
        assert result['total'] == len(result['items']) == 4
    for search in ('no-such-user', "' OR 1=1 --", 'deleted-token'):
        assert e.admin.get(endpoint, params={'search': search}).json() == {'items': [], 'total': 0}
    empty_page = e.admin.get(endpoint, params={'search': alice_name, 'offset': 100}).json()
    assert empty_page == {'items': [], 'total': 4}
    deleted = e.admin.get(endpoint, params={'search': '已删除用户'}).json()
    assert deleted['total'] == 1 and deleted['items'][0]['user_id'] == 'deleted-user'
    assert e.bob.get(endpoint, params={'search': alice_name}).status_code == 403
    assert e.admin.get(endpoint, params={'search': 'x' * 201}).status_code == 422


@pytest.mark.parametrize('days', [7, None])
@pytest.mark.parametrize('change', ['expiry', 'disable', 'password', 'capability', 'global'])
def test_every_request_rechecks_current_access(setup, change, days):
    e = setup
    t = token(e, days=days)
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


@pytest.mark.parametrize('days', [7, None])
def test_kb_permission_removed_and_admin_not_a_bypass(setup, days):
    e = setup
    assert e.alice.put(f'/api/v1/kbs/{e.kid}/members/{e.bob_id}', json={'role': 'reader'}).status_code == 200
    c = external(token(e, e.bob, days=days)['token'])
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


@pytest.mark.parametrize('days', [7, None])
def test_concurrent_token_creation_enforces_account_limit(setup, days):
    from concurrent.futures import ThreadPoolExecutor
    e = setup
    first = token(e, days=days)
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


@pytest.mark.parametrize('slots', [1, 2])
def test_four_overlapping_calls_obey_configured_slots(setup, monkeypatch, slots):
    e = setup
    created = token(e)
    release = threading.Event()
    cfg = store.config().model_copy(update={'max_concurrent': slots})
    monkeypatch.setattr(store, 'config', lambda: cfg)
    def block(token, name, args, check):
        assert release.wait(5), 'test worker was not released'
        check()
        return {'ok': True}
    monkeypatch.setattr(tools, 'execute', block)
    async def scenario():
        with actor(e.alice_id):
            row = store.authenticate(created['token'])
            jobs = [asyncio.create_task(service.call(row, 'list_knowledge_bases', {})) for _ in range(4)]
            try:
                for _ in range(100):
                    if sum(job.done() for job in jobs) == 4-slots and service._active == slots:
                        break
                    await asyncio.sleep(.01)
                assert service._active == slots
                assert sum(job.done() for job in jobs) == 4-slots
            finally:
                release.set()
                results = await asyncio.gather(*jobs, return_exceptions=True)
            success = [r for r in results if isinstance(r, dict)]
            busy = [r for r in results if isinstance(r, HTTPException)]
            assert len(success) == slots and len(busy) == 4-slots
            assert all(r.status_code == 429 and r.detail['code'] == 'busy' for r in busy)
            assert service._active == 0
    asyncio.run(scenario())
