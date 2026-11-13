# ruff: noqa: F811
"""Agent-facing contracts and navigation using isolated real reading indexes."""
import json

import jsonschema
import pytest

from tests.test_accounts_access import env, actor, kb  # noqa: F401
from tests.test_mcp_access import setup, indexed, token, external, invoke  # noqa: F401
from src.mcp import tools, contracts, store
from src.core import accounts as a
from src.db import metadata_db as db
from src.qa.knowledge_tools import KnowledgeTools


def test_model_contract_identity_and_recovery(setup, indexed):
    c = external(token(setup)['token'])
    overview = invoke(c, 'get_kb_overview', kb_id=setup.kid).json()
    assert overview['catalog_scope'] == 'sample_catalog'
    filtered = invoke(c, 'get_kb_overview', kb_id=setup.kid, query='types').json()
    assert filtered['catalog_scope'] == 'query_filename_candidates'
    outline = invoke(c, 'get_document_outline', kb_id=setup.kid, document_id=indexed['id']).json()
    ref = outline['objects'][0]['read_ref']
    first = invoke(c, 'read_document', kb_id=setup.kid, read_ref=ref).json()
    second = invoke(c, 'read_document', kb_id=setup.kid, read_ref=ref).json()
    for value in (overview, filtered, outline, first, second):
        jsonschema.validate(value, contracts.OUTPUT_SCHEMA)
    assert first['evidence'][0]['evidence_id'] == second['evidence'][0]['evidence_id']
    assert first['evidence'][0]['source_id'] == second['evidence'][0]['source_id']
    assert first['request_id'] != second['request_id']
    assert first['evidence'][0]['citation_scope'] == 'call'
    a.execute('UPDATE mcp_references SET expires_at=0')
    failed = invoke(c, 'read_document', kb_id=setup.kid, read_ref=ref)
    assert failed.status_code == 400
    assert failed.json()['detail']['recovery']['action'] == 'obtain_new_reference'


def test_large_directory_pages_are_lossless_and_version_bound(setup, indexed, monkeypatch):
    c = external(token(setup)['token'])
    cfg = store.config().model_copy(update={'max_output_chars': 16000})
    db.set_state('mcp_settings', cfg.model_dump())
    original = KnowledgeTools.execute
    def directory(self, name, args):
        if name != 'list_documents':
            return original(self, name, args)
        return {'documents': [{'document_id': indexed['id'], 'name': f'{i}-'+('x'*2000)} for i in range(25)], 'next_offset': 30}
    monkeypatch.setattr(KnowledgeTools, 'execute', directory)
    result = invoke(c, 'list_documents', kb_id=setup.kid).json()
    assert result['outcome'] == 'partial'
    first_cursor = result['next_cursor']
    seen = []
    for _ in range(20):
        jsonschema.validate(result, contracts.OUTPUT_SCHEMA)
        assert len(json.dumps(result, ensure_ascii=False)) <= 16000
        seen.extend(int(d['name'].split('-')[0]) for d in result['documents'])
        if not result.get('next_cursor'):
            break
        step = result['continuations'][0]
        result = invoke(c, step['tool'], **step['arguments']).json()
    assert seen == list(range(25))
    assert result['continuations'][0]['arguments']['offset'] == 30
    assert result['continuations'][0]['tool'] == 'list_documents'
    other = external(token(setup)['token'])
    assert invoke(other, 'continue_search', kb_id=setup.kid, cursor=first_cursor).status_code == 400
    a.execute('UPDATE knowledge_files SET content_hash=? WHERE id=?', ('changed', indexed['id']))
    stale = invoke(c, 'continue_search', kb_id=setup.kid, cursor=first_cursor).json()
    assert stale['code'] == 'document_changed'


def test_catalog_query_and_paging_not_evidence(setup):
    c = external(token(setup, kids=[setup.kid, setup.other])['token'])
    first = invoke(c, 'list_knowledge_bases', limit=1).json()
    assert len(first['knowledge_bases']) == 1
    step = first['continuations'][0]
    second = invoke(c, step['tool'], **step['arguments']).json()
    assert first['knowledge_bases'][0]['kb_id'] != second['knowledge_bases'][0]['kb_id']
    assert second['next_offset'] is None
    empty = invoke(c, 'list_knowledge_bases', query='does not exist').json()
    assert empty['knowledge_bases'] == []


def test_schema_exposes_optional_query_and_output():
    definitions = tools.definitions()
    for tool in definitions:
        jsonschema.Draft202012Validator.check_schema(tool['inputSchema'])
        jsonschema.Draft202012Validator.check_schema(tool['outputSchema'])
    overview = next(t for t in definitions if t['name'] == 'get_kb_overview')
    assert 'query' in overview['inputSchema']['properties']
    assert 'query' not in overview['inputSchema']['required']


def test_long_read_remains_contiguous_under_small_output_budget(setup, tmp_path):
    from src.knowledge import reading_index as ri
    path = tmp_path / 'long.md'
    text = '# Content\n' + ''.join(f'row {i} ' + '内容'*15 + '\n' for i in range(1000))
    path.write_text(text)
    fid = db.upsert_file(path.name, str(path), ri.fingerprint(path), path.stat().st_size, 'md', kb_id=setup.kid)
    db.set_file_processed(fid, [])
    file = db.get_file_by_path(path.name, setup.kid)
    builder = ri.build_file(file)
    with db.get_cursor() as cur:
        builder.publish(cur, fid, file['content_hash'])
    db.set_state('mcp_settings', store.Settings(enabled=True, max_output_chars=16000, requests_per_minute=120).model_dump())
    c = external(token(setup)['token'])
    outline = invoke(c, 'get_document_outline', kb_id=setup.kid, document_id=fid).json()
    ref = outline['sections'][0]['read_ref']
    ends, content = [], ''
    for _ in range(20):
        result = invoke(c, 'read_document', kb_id=setup.kid, read_ref=ref).json()
        assert len(json.dumps(result, ensure_ascii=False)) <= 16000
        ev = result['evidence'][0]
        if ends:
            assert ev['reading']['start'] == ends[-1]
        ends.append(ev['reading']['end'])
        content += ev['text']
        ref = result.get('next_read_ref')
        if not ref:
            break
    with ri.open_index(file) as conn:
        raw = conn.execute('SELECT text FROM sections WHERE id=?', (outline['sections'][0]['section'],)).fetchone()[0]
    assert content == raw


@pytest.mark.parametrize("giant_first_row", [False, True])
def test_table_budget_preserves_rows_and_citations(setup, tmp_path, giant_first_row):
    from src.knowledge import reading_index as ri
    from src.knowledge.parsers.base import ParsedSection
    path = tmp_path / 'rows.xlsx'
    path.write_bytes(b'fixture')
    fid = db.upsert_file(path.name, str(path), ri.fingerprint(path), 7, 'xlsx', kb_id=setup.kid)
    db.set_file_processed(fid, [])
    file = db.get_file_by_path(path.name, setup.kid)
    builder = ri.Builder()
    text, cells = '', []
    for number in range(1, 41):
        value = f'row{number} ' + 'cell '*(10000 if giant_first_row and number == 1 else 100)
        start = len(text)
        text += value+'\n'
        cells.append({'row': number, 'values': {'1': value}, 'start': start, 'end': len(text)-1})
    builder.add(ParsedSection(text, path, 0, 'Data', {'sheet': 'Data', 'cell_rows': cells, 'merged_ranges': []}))
    builder.finish()
    with db.get_cursor() as cur:
        builder.publish(cur, fid, file['content_hash'])
    db.set_state('mcp_settings', store.Settings(enabled=True, max_output_chars=16000, requests_per_minute=120).model_dump())
    c = external(token(setup)['token'])
    outline = invoke(c, 'get_document_outline', kb_id=setup.kid, document_id=fid).json()
    arguments = {'kb_id': setup.kid, 'scope_ref': outline['sheets'][0]['scope_ref'], 'row_start': 1, 'row_end': 40}
    numbers = []
    for _ in range(40):
        result = invoke(c, 'read_table_rows', **arguments).json()
        assert 'error' not in result, result
        assert len(json.dumps(result, ensure_ascii=False)) <= 16000
        cited = {ev['citation']: ev['text'] for ev in result['evidence']}
        for row in result['rows']:
            assert row['values']['1'] in cited[row['citation']]
            numbers.append(row['row'])
        if giant_first_row and not numbers:
            pending = result['pending_row']
            assert pending['row'] == 1
            text = ''
            read_ref = pending['read_ref']
            for _ in range(30):
                read = invoke(c, 'read_document', kb_id=setup.kid, read_ref=read_ref).json()
                assert len(json.dumps(read, ensure_ascii=False)) <= 16000
                text += ''.join(ev['text'] for ev in read['evidence'])
                read_ref = read.get('next_read_ref')
                if not read_ref:
                    break
            assert text == 'row1 ' + 'cell '*10000
            numbers.append(1)
        steps = [s for s in result['continuations'] if s['kind'] in ('table_rows', 'rows_after_pending')]
        if not steps:
            break
        assert steps[0]['arguments']['row_start'] > arguments['row_start']
        arguments = steps[0]['arguments']
    assert numbers == list(range(1, 41))


def test_bounded_scan_visits_small_files_and_resumes_without_losing_matches(setup, tmp_path, monkeypatch):
    from src.knowledge import reading_index as ri
    from src.mcp import search
    # Boundaries intentionally cross a symbol; output windows merge nearby matches.
    monkeypatch.setattr(search, 'SLICE_CHARS', 128)
    monkeypatch.setattr(search, 'CALL_CHARS', 256)
    monkeypatch.setattr(search, 'MAX_MATCHES', 8)
    docs = {}
    for name, content in [('a-big.md', 'prefix\n'+('value target_field end\n'*80)), ('z-small.md', 'Only target_field here\n')]:
        path = tmp_path/name
        path.write_text(content)
        fid = db.upsert_file(name, str(path), ri.fingerprint(path), path.stat().st_size, 'md', kb_id=setup.kid)
        db.set_file_processed(fid, [])
        file = db.get_file_by_path(name, setup.kid)
        builder = ri.build_file(file)
        with db.get_cursor() as cur:
            builder.publish(cur, fid, file['content_hash'])
        docs[fid] = content
    db.set_state('mcp_settings', store.Settings(enabled=True, requests_per_minute=120).model_dump())
    c = external(token(setup)['token'])
    result = invoke(c, 'search_knowledge', kb_id=setup.kid, query='target_field', match_mode='identifier', limit=20).json()
    assert len({e['document_id'] for e in result['evidence']}) == 2
    assert result['coverage']['scan_complete'] is False
    assert result['coverage']['call_matches'] > len(result['evidence'])
    seen = set()
    pages = 0
    while True:
        pages += 1
        for ev in result['evidence']:
            for start, end in ev['match_positions']:
                match = (ev['document_id'], ev['section'], start, end)
                assert match not in seen
                seen.add(match)
        if not result['continuations']:
            break
        step = result['continuations'][0]
        result = invoke(c, step['tool'], **step['arguments']).json()
        assert 'error' not in result, result
        assert pages < 60
    assert len(seen) == 81
    assert result['coverage']['scan_complete'] is True
    assert result['coverage']['documents_completed'] == 2
    assert result['coverage']['total_matches'] == 81


def test_ranked_variants_retrieve_target_and_reject_stale_chunks(setup, tmp_path, monkeypatch):
    from src.knowledge import reading_index as ri
    from src.mcp import ranked_search
    docs = []
    raw = {}
    for name, content, count in [('records.xml', '<record>general data</record>', 10), ('burn_firmware.sh', 'echo burn the firmware failed', 1)]:
        path = tmp_path/name
        path.write_text(content)
        fid = db.upsert_file(name, str(path), ri.fingerprint(path), path.stat().st_size, 'txt', kb_id=setup.kid)
        ids = [f'{fid}_{i}' for i in range(count)]
        db.set_file_processed(fid, ids)
        file = db.get_file_by_path(name, setup.kid)
        builder = ri.build_file(file)
        with db.get_cursor() as cur:
            builder.publish(cur, fid, file['content_hash'])
        docs.append(file)
        for i, cid in enumerate(ids):
            raw[cid] = {'id': cid, 'file_id': fid, 'text': content, 'content_hash': file['content_hash'], 'chunk_index': i, 'section_index': 0}
    vectors_called = []
    class Embedder:
        def embed(self, queries):
            vectors_called.append(queries)
            return [[i] for i in range(len(queries))], 'fake'
    monkeypatch.setattr(ranked_search.llm_client, 'get_client', lambda: Embedder())
    def vector(vector, k, where=None, **kwargs):
        values = list(raw.values())
        if where:
            values = [v for v in values if v['file_id'] == where['file_id']]
        elif vector == [1]:
            values = [v for v in values if v['file_id'] == docs[1]['id']]
        return [{**v, 'score': .9} for v in values]
    monkeypatch.setattr(ranked_search.vector_store, 'query_by_embedding', vector)
    monkeypatch.setattr(ranked_search.bm25_index, 'query_enhanced', lambda q, *args: [v for v in raw.values() if 'firmware' in q and v['file_id'] == docs[1]['id']])
    monkeypatch.setattr(tools.AccessTools, 'live_chunks', lambda self, ids: {cid: raw[cid] for cid in ids if cid in raw})
    c = external(token(setup)['token'])
    result = invoke(c, 'search_knowledge', kb_id=setup.kid, query='固件烧录失败', query_variants=['firmware failed'], match_mode='hybrid', limit=3).json()
    assert 'error' not in result, result
    assert docs[1]['id'] in {ev['document_id'] for ev in result['evidence']}
    assert vectors_called == [['固件烧录失败', 'firmware failed']]
    assert result['query_variants_used'] == vectors_called[0]
    # A small document absent from global top-K still gets a bounded source probe;
    # relevance, not file identity, decides which candidates reach the caller.
    from src.mcp import relevance
    db.set_state('mcp_retrieval_settings', {'rerank_enabled': True, 'rerank_candidates': 4})
    original_vector = vector
    def dominated(vector, k, where=None, **kwargs):
        return original_vector(vector, k, where=where or {'file_id': docs[0]['id']}, **kwargs)
    monkeypatch.setattr(ranked_search.vector_store, 'query_by_embedding', dominated)
    monkeypatch.setattr(relevance, 'score', lambda q, texts: [.9 if 'firmware' in text else .001 for text in texts])
    rescued = invoke(c, 'search_knowledge', kb_id=setup.kid, query='设备更新失败', match_mode='semantic', limit=3).json()
    assert {ev['document_id'] for ev in rescued['evidence']} == {docs[1]['id']}
    assert rescued['coverage']['targeted_source_probes'] == 1
    assert rescued['relevance']['status'] == 'assessed'
    # A summary must not count as body coverage, even when it carries the target file id.
    calls = []
    summary = {'id': 'summary-only', 'file_id': docs[1]['id'], 'chunk_type': 'summary', 'score': .99}
    def summary_dominated(vector, k, where=None, **kwargs):
        calls.append((where, k))
        if where and where['file_id'] == docs[1]['id']:
            if k == 4:
                return [summary]
            return original_vector(vector, k, where=where, **kwargs)
        return [summary, *original_vector(vector, k, where={'file_id': docs[0]['id']}, **kwargs)]
    monkeypatch.setattr(ranked_search.vector_store, 'query_by_embedding', summary_dominated)
    repaired = invoke(c, 'search_knowledge', kb_id=setup.kid, query='设备更新失败', match_mode='semantic').json()
    assert {ev['document_id'] for ev in repaired['evidence']} == {docs[1]['id']}
    assert ({'file_id': docs[1]['id']}, 16) in calls
    # Busy inference exposes leads separately, never as assessed evidence; cursor keeps the distinction.
    def busy(*args):
        raise relevance.RelevanceUnavailable('reranker_busy')
    monkeypatch.setattr(relevance, 'score', busy)
    unavailable = invoke(c, 'search_knowledge', kb_id=setup.kid, query='设备更新失败', match_mode='semantic', limit=1).json()
    assert unavailable['evidence'] == [] and unavailable['unassessed_candidates']
    assert unavailable['relevance']['reason'] == 'reranker_busy'
    assert unavailable['outcome'] == 'partial' and unavailable['search_scope']['degraded']
    assert unavailable['unassessed_candidates'][0]['source_id']
    more = invoke(c, 'continue_search', kb_id=setup.kid, cursor=unavailable['next_cursor']).json()
    assert more['evidence'] == [] and more['unassessed_candidates']
    assert more['relevance']['reason'] == 'reranker_busy'
    monkeypatch.setattr(relevance, 'score', lambda q, texts: [.9 if 'firmware' in text else .001 for text in texts])
    # A stale chunk is not evidence even when the vector service ranks it highly.
    for value in raw.values():
        if value['file_id'] == docs[1]['id']:
            value['content_hash'] = 'stale'
    again = invoke(c, 'search_knowledge', kb_id=setup.kid, query='firmware', match_mode='semantic').json()
    assert all(ev['document_id'] != docs[1]['id'] for ev in again['evidence'])
    denied = invoke(c, 'search_knowledge', kb_id=setup.kid, query='firmware', query_variants=['固件'], match_mode='exact')
    assert denied.status_code == 422


def test_ranked_unavailable_is_not_no_knowledge(setup, monkeypatch):
    from src.mcp import ranked_search
    def fail(*args, **kwargs):
        raise RuntimeError('fixture')
    monkeypatch.setattr(ranked_search.llm_client, 'get_client', fail)
    monkeypatch.setattr(ranked_search.bm25_index, 'query_enhanced', fail)
    c = external(token(setup)['token'])
    result = invoke(c, 'search_knowledge', kb_id=setup.kid, query='anything', match_mode='hybrid').json()
    assert result['outcome'] == 'error'
    assert result['code'] == 'index_unavailable'
    assert len(result['retrieval_warnings']) == 2


def test_embedding_failure_is_visible_in_result_and_diagnostics(setup, indexed, monkeypatch):
    from src.mcp import ranked_search
    from src.core.llm_client import EmbeddingLoadError
    class Missing:
        def embed(self, queries):
            raise EmbeddingLoadError(60, 'embedding_files_missing')
    monkeypatch.setattr(ranked_search.llm_client, 'get_client', lambda: Missing())
    monkeypatch.setattr(ranked_search.bm25_index, 'query_enhanced', lambda *a: [])
    c = external(token(setup)['token'])
    result = invoke(c, 'search_knowledge', kb_id=setup.kid, query='concept', match_mode='semantic').json()
    assert result['search_scope']['executed_paths'] == ['keyword']
    assert result['search_scope']['degraded'] is True
    warning = result['retrieval_warnings'][0]
    assert warning['code'] == 'embedding_files_missing'
    assert warning['stage'] == 'embedding'
    assert warning['retry_after_seconds'] == 60
    assert 'mcp_embedding' in result['diagnostics']['phase_ms']
    assert result['diagnostics']['request_log_key'] == result['request_id']
    assert 'query_variants' in result['retrieval_guidance']


def test_mcp_relevance_settings_are_independent_and_old_client_safe(setup):
    from src.mcp.store import RETRIEVAL_KEYS
    c = setup.admin
    data = c.get('/api/v1/admin/mcp/settings').json()
    data.update(rerank_enabled=True, rerank_min_score=.2, rerank_candidates=12)
    saved = c.put('/api/v1/admin/mcp/settings', json=data)
    assert saved.status_code == 200
    assert not (RETRIEVAL_KEYS & db.get_state('mcp_settings').keys())
    old = {key: value for key, value in data.items() if key not in RETRIEVAL_KEYS}
    again = c.put('/api/v1/admin/mcp/settings', json=old)
    assert again.status_code == 200
    assert again.json()['rerank_min_score'] == .2
    assert again.json()['rerank_candidates'] == 12


def test_unassessed_output_pages_preserve_leads_and_partial_status(setup, indexed, monkeypatch):
    cfg = store.config().model_copy(update={'max_output_chars': 16000})
    db.set_state('mcp_settings', cfg.model_dump())
    original = KnowledgeTools.execute
    def large(self, name, args):
        if name != 'list_documents':
            return original(self, name, args)
        return {'status': 'partial', 'evidence': [], 'relevance': {'status': 'unavailable', 'reason': 'reranker_busy'},
                'unassessed_candidates': [{'document_id': indexed['id'], 'version': indexed['content_hash'],
                    'text': f'{i}:'+('x'*2000), 'relevance_status': 'unassessed'} for i in range(25)]}
    monkeypatch.setattr(KnowledgeTools, 'execute', large)
    c = external(token(setup)['token'])
    result = invoke(c, 'list_documents', kb_id=setup.kid).json()
    seen = []
    for _ in range(25):
        jsonschema.validate(result, contracts.OUTPUT_SCHEMA)
        assert result['outcome'] == 'partial' and result['evidence'] == []
        assert len(json.dumps(result, ensure_ascii=False)) <= 16000
        seen.extend(int(ev['text'].split(':')[0]) for ev in result['unassessed_candidates'])
        assert all(ev['source_id'] and ev['relevance_status'] == 'unassessed' for ev in result['unassessed_candidates'])
        if not result.get('next_cursor'):
            break
        result = invoke(c, 'continue_search', kb_id=setup.kid, cursor=result['next_cursor']).json()
    assert seen == list(range(25))
