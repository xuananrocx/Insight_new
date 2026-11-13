"""Relevance policy and recovery use synthetic candidates, never model downloads."""
from types import SimpleNamespace

import pytest

from src.mcp import relevance, store


def kb():
    return SimpleNamespace(check=lambda: None, timings={})


def hits():
    return [{'id': str(i), 'file_id': i % 3, 'text': f'passage {i}'} for i in range(12)]


def test_diverse_pool_deduplicates_only_within_source():
    rows = [{'id': 'a', 'file_id': 1, 'text': 'same text'}, {'id': 'b', 'file_id': 1, 'text': 'same  text'},
            {'id': 'c', 'file_id': 2, 'text': 'same text'}, {'id': 'd', 'file_id': 1, 'text': 'different'}]
    assert [h['id'] for h in relevance.select(rows, 2)] == ['a', 'c']


def test_relevance_does_not_fill_limit_or_skip_singleton(monkeypatch):
    monkeypatch.setattr(store, 'config', lambda: store.Settings(rerank_candidates=4))
    monkeypatch.setattr(relevance, 'score', lambda q, texts: [.01] * len(texts))
    result, meta = relevance.assess(kb(), 'question', hits()[:1])
    assert result == [] and meta['scored_count'] == 1 and meta['accepted_count'] == 0
    assert meta['status'] == 'assessed'


def test_disabled_preserves_candidates_without_loading(monkeypatch):
    monkeypatch.setattr(store, 'config', lambda: store.Settings(rerank_enabled=False))
    monkeypatch.setattr(relevance, 'score', lambda *a: pytest.fail('disabled must not load model'))
    original = hits()
    result, meta = relevance.assess(kb(), 'q', original)
    assert result == original and meta['status'] == 'disabled'


def test_inference_failure_is_explicit_and_does_not_filter(monkeypatch):
    monkeypatch.setattr(store, 'config', lambda: store.Settings())
    def unavailable(*args):
        raise FileNotFoundError('secret path')
    monkeypatch.setattr(relevance, 'score', unavailable)
    original = hits()
    result, meta = relevance.assess(kb(), 'q', original)
    assert result == original and meta['status'] == 'unavailable'
    assert 'secret' not in str(meta) and meta['scored_count'] == 0


def test_partial_scoring_never_labels_unassessed_as_relevant(monkeypatch):
    monkeypatch.setattr(store, 'config', lambda: store.Settings())
    count = [0]
    def scorer(query, texts):
        count[0] += 1
        if count[0] > 1:
            raise RuntimeError('busy')
        return [.9] + [.01]*(len(texts)-1)
    monkeypatch.setattr(relevance, 'score', scorer)
    result, meta = relevance.assess(kb(), 'q', hits())
    assert len(result) == 1 and result[0]['relevance_score'] == .9
    assert meta['status'] == 'partial' and meta['unassessed_count'] == 8


def test_invalid_scores_do_not_create_false_relevance(monkeypatch):
    monkeypatch.setattr(store, 'config', lambda: store.Settings())
    monkeypatch.setattr(relevance, 'score', lambda q, texts: [float('nan')]*len(texts))
    result, meta = relevance.assess(kb(), 'q', hits())
    assert meta['status'] == 'unavailable' and all('relevance_score' not in h for h in result)


def test_soft_deadline_does_not_append_unscored_candidates(monkeypatch):
    clock = [0.]
    monkeypatch.setattr(store, 'config', lambda: store.Settings(rerank_seconds=2))
    monkeypatch.setattr(relevance.time, 'monotonic', lambda: clock[0])
    def slow(query, texts):
        clock[0] += 3
        return [.8]*len(texts)
    monkeypatch.setattr(relevance, 'score', slow)
    result, meta = relevance.assess(kb(), 'q', hits())
    assert len(result) == 4
    assert meta['reason'] == 'time_budget' and meta['scored_count'] == 4
    assert meta['unassessed_count'] == 8


def test_cancellation_propagates_at_batch_boundary(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(store, 'config', lambda: store.Settings())
    state = [False]
    def check():
        if state[0]:
            raise HTTPException(408, 'cancelled')
    def scorer(query, texts):
        state[0] = True
        return [.8]*len(texts)
    monkeypatch.setattr(relevance, 'score', scorer)
    with pytest.raises(HTTPException) as exc:
        relevance.assess(SimpleNamespace(check=check, timings={}), 'q', hits())
    assert exc.value.status_code == 408


def test_busy_reason_is_stable_and_retryable(monkeypatch):
    monkeypatch.setattr(store, 'config', lambda: store.Settings())
    with relevance._lock:
        result, meta = relevance.assess(kb(), 'q', hits())
    assert meta['reason'] == 'reranker_busy' and meta['retryable']
    assert meta['status'] == 'unavailable' and meta['scored_count'] == 0
    assert result == hits()


def test_cancellation_inside_scorer_is_not_downgraded(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(store, 'config', lambda: store.Settings())
    def cancelled(*args):
        raise HTTPException(408, 'cancelled')
    monkeypatch.setattr(relevance, 'score', cancelled)
    with pytest.raises(HTTPException):
        relevance.assess(kb(), 'q', hits())


def test_valid_candidates_reject_summary_stale_foreign_and_removed():
    from src.mcp.ranked_search import valid_candidates
    file = {'id': 1, 'content_hash': 'current'}
    rows = {'good': {'file_id': 1, 'content_hash': 'current'},
            'summary': {'file_id': 1, 'chunk_type': 'summary'},
            'stale': {'file_id': 1, 'content_hash': 'old'},
            'foreign': {'file_id': 2}, 'removed': {'file_id': 1}}
    context = kb()
    context.live_chunks = lambda ids: {cid: rows[cid] for cid in ids if cid in rows}
    owners = {cid: file for cid in rows if cid != 'removed'}
    result = valid_candidates(context, [[{'id': cid} for cid in rows]], owners)
    assert set(result) == {'good'}


def test_document_lexical_scope_applies_before_candidate_cap():
    from src.qa import bm25_index
    import uuid
    from src.db import metadata_db
    metadata_db.init_db()
    kid = uuid.uuid4().hex
    chunks = [{'id': f'{kid}-{i}', 'text': 'needle', 'metadata': {'kb_id': kid, 'file_id': 1}} for i in range(240)]
    target = kid+'-target'
    chunks.append({'id': target, 'text': 'needle with additional text making global rank lower',
                   'metadata': {'kb_id': kid, 'file_id': 2}})
    bm25_index.add_chunks(chunks)
    try:
        assert target not in {h['id'] for h in bm25_index.query_enhanced('needle', 120, kid)}
        assert [h['id'] for h in bm25_index.query_enhanced('needle', 120, kid, allowed_ids={target})] == [target]
        assert bm25_index.query_enhanced('needle', 120, kid, allowed_ids=set()) == []
    finally:
        bm25_index.remove_chunks([c['id'] for c in chunks])


def test_source_probe_budget_is_bounded_and_reports_uncovered(monkeypatch):
    from src.mcp import ranked_search
    files = [{'id': i, 'status': 'done', 'relative_path': f'{i}.txt', 'content_hash': 'v'} for i in range(12)]
    context = kb()
    context.kb_id = 'isolated'
    context.files = lambda: files
    context.ids = lambda f: [f"body-{f['id']}"]
    context.live_chunks = lambda ids: {}
    calls = []
    def vectors(*args, **kwargs):
        calls.append(kwargs)
        return []
    monkeypatch.setattr(ranked_search.db, 'get_kb', lambda kid: {'collection_name': 'isolated'})
    monkeypatch.setattr(ranked_search.llm_client, 'get_client', lambda: SimpleNamespace(embed=lambda q: ([[0]], 'fake')))
    monkeypatch.setattr(ranked_search.vector_store, 'query_by_embedding', vectors)
    monkeypatch.setattr(store, 'config', lambda: store.Settings())
    monkeypatch.setattr(ranked_search.reading_index, 'info', lambda f: None)
    def save(k, snapshot):
        k.search_pages = {'key': snapshot}
        return 'key'
    monkeypatch.setattr(ranked_search.tool_navigation, 'save', save)
    monkeypatch.setattr(ranked_search, 'page', lambda k, key, snapshot, offset, limit: snapshot)
    result = ranked_search.search(context, {'query': 'unknown', 'match_mode': 'semantic'}, [])
    assert result['probe_eligible'] == 12 and result['source_probes'] == 8
    assert len(calls) == 17  # global + eight initial probes + at most one refill per source
    assert all(c['k'] <= 16 for c in calls[1:])


@pytest.mark.parametrize('weight', ['model.safetensors', 'pytorch_model.bin'])
def test_offline_bundle_model_layout(monkeypatch, tmp_path, weight):
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, 'try_to_load_from_cache', lambda *a, **kw: None)
    monkeypatch.setenv('HF_HOME', str(tmp_path/'huggingface'))
    folder = tmp_path/'reranker-torch'
    folder.mkdir()
    for name in ('config.json', 'tokenizer.json', weight):
        (folder/name).write_text('fixture')
    assert relevance.model_path() == folder
    (folder/weight).unlink()
    with pytest.raises(FileNotFoundError):
        relevance.model_path()
