"""Scope handles and original-text search, using isolated metadata and artifacts."""
import uuid
from types import SimpleNamespace

import pytest

from src.core import accounts, llm_client, vector_store
from src.db import metadata_db as db
from src.knowledge import reading_index as ri
from src.qa import canonical_search
from src.qa.knowledge_tools import KnowledgeTools


def publish(file):
    builder = ri.build_file(file)
    with db.get_cursor() as cur:
        builder.publish(cur, file['id'], file['content_hash'])


@pytest.fixture
def make_document(tmp_path, monkeypatch):
    db.init_db()
    monkeypatch.setattr(accounts, 'enabled', False)
    def make(text, ids=()):
        kid = uuid.uuid4().hex
        db.create_kb(kid, 'scope test', kid)
        path = tmp_path / f'{kid}.h'
        path.write_text(text, encoding='utf8')
        fid = db.upsert_file(path.name, str(path), ri.fingerprint(path),
                             path.stat().st_size, 'h', kb_id=kid)
        db.set_file_processed(fid, list(ids))
        file = db.get_file_by_path(path.name, kid)
        publish(file)
        return file, KnowledgeTools(kid)
    return make


def scope(kb, file):
    result = kb.execute('get_document_outline', {'document_id': file['id']})
    assert 'error' not in result, result
    return result['scope_ref']


def test_exact_original_search_stable_pages_without_vector_ids(make_document, monkeypatch):
    file, kb = make_document(''.join(f'row{i} Needle marker\n' + 'z' * 1300 + '\n' for i in range(7)))
    args = {'scope_ref': scope(kb, file), 'query': 'Needle', 'match_mode': 'exact', 'limit': 2}
    first = kb.execute('search_scope', args)
    assert first['matching_chunks'] == 7
    assert first['coverage']['scan_complete']
    def unexpected(*args, **kwargs):
        pytest.fail('continuation must not repeat retrieval')
    monkeypatch.setattr(canonical_search, 'search', unexpected)
    items = list(first['evidence'])
    page = first
    while page.get('next_cursor'):
        page = kb.execute('continue_search', {'cursor': page['next_cursor']})
        assert 'error' not in page, page
        items.extend(page['evidence'])
    assert len(items) == 7
    assert [i['match_start'] for i in items] == sorted(i['match_start'] for i in items)
    assert len({i['reading']['start'] for i in items}) == 7


def test_scope_foreign_and_rebuilt_artifact_rejected_even_after_cached_call(make_document):
    file, kb = make_document('struct Item { int amount; };')
    ref = scope(kb, file)
    args = {'scope_ref': ref, 'query': 'amount', 'match_mode': 'exact'}
    assert kb.execute('search_scope', args)['status'] == 'found'
    other = KnowledgeTools(file['kb_id'])
    assert 'error' in other.execute('search_scope', args)
    publish(file)
    assert 'error' in kb.execute('search_scope', args)


def test_identical_text_at_distinct_coordinates_keeps_distinct_citations(make_document):
    block = 'q' * 300 + ' Needle ' + 'q' * 800
    file, kb = make_document(block * 3)
    result = kb.execute('search_scope', {'scope_ref': scope(kb, file), 'query': 'Needle', 'match_mode': 'exact'})
    values = result['evidence']
    assert len(values) == 3
    assert values[0]['text'] == values[1]['text'] == values[2]['text']
    assert len({v['citation'] for v in values}) == 3
    assert len({v['reading']['start'] for v in values}) == 3


@pytest.mark.parametrize('mode', ['semantic', 'hybrid'])
def test_semantic_only_candidates_survive_and_keep_vector_ranking(make_document, monkeypatch, mode):
    first = 'Earlier document passage about orange.'
    second = 'Later document passage about purple.'
    file, kb = make_document(first + '\n' + second, ['earlier', 'later'])
    monkeypatch.setattr(llm_client, 'get_client', lambda: SimpleNamespace(embed=lambda q: ([[0.1]], 'mock')))
    monkeypatch.setattr(vector_store, 'query_by_embedding', lambda *a, **kw: [
        {'id': 'later', 'text': second}, {'id': 'earlier', 'text': first}])
    result = kb.execute('search_scope', {'scope_ref': scope(kb, file), 'query': 'unrelatedneedle', 'match_mode': mode})
    assert 'error' not in result, result
    assert [e['text'] for e in result['evidence']] == [second, first]


def test_invalid_scope_and_unknown_fields_never_widen_search(make_document, monkeypatch):
    file, kb = make_document('Needle')
    ref = scope(kb, file)
    def unexpected(*args, **kwargs):
        pytest.fail('invalid parameters must not run a wider search')
    monkeypatch.setattr(canonical_search, 'search', unexpected)
    assert 'error' in kb.execute('search_scope', {'scope_ref': 'scope_missing', 'query': 'Needle'})
    assert 'error' in kb.execute('search_scope', {'scope_ref': ref, 'query': 'Needle', 'sheet': 'none'})


def test_search_cursor_rejected_after_same_hash_rebuild(make_document):
    file, kb = make_document(('x' * 400 + ' Needle ' + 'x' * 800) * 3)
    result = kb.execute('search_scope', {'scope_ref': scope(kb, file), 'query': 'Needle', 'match_mode': 'exact', 'limit': 1})
    cursor = result['next_cursor']
    assert cursor
    publish(file)
    assert 'error' in kb.execute('continue_search', {'cursor': cursor})
