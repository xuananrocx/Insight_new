import json
import uuid
from types import SimpleNamespace

import pytest
from src.core import accounts
from src.core.llm_providers import AnthropicProvider, ChatText
from src.db import metadata_db as db
from src.knowledge import summary_tasks as jobs, ai_summarizer as summary
from src.knowledge.chunker import Chunk


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    db.init_db()
    jobs.init_schema()
    monkeypatch.setattr(accounts, 'enabled', False)
    monkeypatch.setattr(jobs._pool, 'submit', lambda *args: None)
    jobs._stopping.clear()


def make_kb(count=1, summaries=True):
    kb = 'jobs-' + uuid.uuid4().hex
    db.create_kb(kb, 'Jobs', 'col-' + kb, source='user')
    files = []
    for i in range(count):
        fid = db.upsert_file(relative_path=f'{i}.txt', absolute_path=f'/tmp/{i}.txt', content_hash=f'h{i}', file_size=10, file_type='txt', kb_id=kb)
        db.set_file_processed(fid, [f'c{i}'])
        db.upsert_document_meta(file_id=fid, kb_id=kb, processed_level='summarized' if summaries else 'raw', summary=f'已有摘要 {i}' if summaries else None)
        files.append(fid)
    return kb, files


def run(job):
    with db.get_cursor() as cur:
        row = cur.execute('SELECT * FROM summary_jobs WHERE id=?', (job['id'],)).fetchone()
    jobs.run(job['id'], job['kb_id'], json.loads(row['snapshot']), row['signature'])
    return jobs.get(job['id'], job['kb_id'])


class Client:
    def __init__(self):
        self.inputs = []
    def chat(self, messages, **kw):
        self.inputs.append(messages)
        return '完整的全局摘要，涵盖知识库中所有文档。' * 8, 'fake'


def test_rebuild_reuses_all_35_documents(monkeypatch):
    kb, _ = make_kb(35)
    client = Client()
    monkeypatch.setattr(summary.llm_client, 'get_client', lambda: client)
    monkeypatch.setattr(summary, 'summarize_and_extract', lambda **kw: pytest.fail('must reuse'))
    job = jobs.start(kb)
    assert jobs.start(kb)['id'] == job['id']
    result = run(job)
    assert result['state'] == 'done'
    assert result['reused'] == 35
    assert '34.txt' in client.inputs[-1][1]['content']
    assert not db.is_kb_summary_stale(kb)


def test_cancel_queued_makes_no_calls(monkeypatch):
    kb, _ = make_kb()
    job = jobs.start(kb)
    jobs.cancel(job['id'], kb)
    monkeypatch.setattr(summary.llm_client, 'get_client', lambda: pytest.fail('cancelled'))
    assert run(job)['state'] == 'cancelled'


def test_changed_snapshot_does_not_replace_old_summary(monkeypatch):
    kb, files = make_kb()
    db.update_kb_global_summary(kb, '旧摘要', 'old', 1)
    job = jobs.start(kb)
    with db.get_cursor() as cur:
        cur.execute("UPDATE knowledge_files SET content_hash='changed' WHERE id=?", (files[0],))
    assert run(job)['state'] == 'failed'
    assert db.get_kb_global_summary(kb)['summary'] == '旧摘要'


def test_failed_document_stops_and_retains_global(monkeypatch):
    kb, _ = make_kb(2, summaries=False)
    db.update_kb_global_summary(kb, '旧摘要', 'old', 1)
    monkeypatch.setattr(summary, '_fetch_chunks_by_ids', lambda *a: [Chunk('text', {})])
    calls = []
    def failed(**kw):
        calls.append(kw['file_id'])
        return summary.SummarizeResult(error='空响应')
    monkeypatch.setattr(summary, 'summarize_and_extract', failed)
    result = run(jobs.start(kb))
    assert result['state'] == 'failed'
    assert len(calls) == 1
    assert db.get_kb_global_summary(kb)['summary'] == '旧摘要'


def test_known_stale_doc_is_not_reused():
    kb, files = make_kb()
    file = db.list_files_in_kb(kb)[0]
    meta = db.list_document_meta_by_kb(kb)[0]
    jobs.record_version(files[0], 'previous-hash', meta['summary'])
    assert not jobs.valid_summary(file, meta)


def test_json_braces_and_schema():
    assert summary._parse_llm_response(json.dumps({'summary': '字段 { 和 }', 'concepts': []}))['summary'] == '字段 { 和 }'
    for payload in ({'summary': 3}, {'summary': 'ok', 'concepts': {}}):
        with pytest.raises(ValueError):
            summary._parse_llm_response(json.dumps(payload))


def test_strict_segment_bound():
    chunks = [Chunk('x' * 900, {}), Chunk('y' * 80, {})]
    parts = summary._split_chunks_by_sections(chunks, 100)
    assert all(len('\n\n'.join(c.text for c in part)) <= 100 for part in parts)
    assert ''.join(c.text for p in parts for c in p) == 'x' * 900 + 'y' * 80


def test_proven_overlap_only():
    a = Chunk('abcdef', dict(source_path='a', section_index=0, source_char_start=0, source_char_end=6))
    b = Chunk('defghi', dict(source_path='a', section_index=0, source_char_start=3, source_char_end=9))
    assert [c.text for c in summary._without_source_overlap([a,b])] == ['abcdef', 'ghi']
    assert [c.text for c in summary._without_source_overlap([Chunk('abcdef',{}),Chunk('defghi',{})])] == ['abcdef','defghi']


def test_anthropic_keeps_all_text_and_reason():
    response = SimpleNamespace(content=[SimpleNamespace(text='first',type='text'), SimpleNamespace(text='second',type='text')], stop_reason='max_tokens', usage=SimpleNamespace(input_tokens=10,output_tokens=20),model='fake')
    fake = SimpleNamespace(chat_model='fake', name='fake', _convert_to_anthropic_format=lambda _: ([], ''), client=SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: response)))
    answer = AnthropicProvider.chat(fake, [])
    assert answer == 'firstsecond' and answer.stop_reason == 'max_tokens'
    assert answer.usage['output_tokens'] == 20


def test_truncated_retry_bounded():
    calls = []
    def chat(*args, **kw):
        calls.append(kw['max_tokens'])
        return ChatText('partial', stop_reason='max_tokens'), 'fake'
    with pytest.raises(ValueError):
        summary._checked_chat(SimpleNamespace(chat=chat), [], max_tokens=2000)
    assert calls == [2000, 4000]


def test_checkpoint_survives_failed_attempt(monkeypatch):
    kb, _ = make_kb(summaries=False)
    monkeypatch.setattr(summary, '_fetch_chunks_by_ids', lambda *a: [Chunk('text', {})])
    seen = []
    def summarize(**kw):
        cp = kw['checkpoint']
        seen.append(cp('same segment', None))
        cp('same segment', 'saved note')
        return summary.SummarizeResult(error='later failure')
    monkeypatch.setattr(summary, 'summarize_and_extract', summarize)
    run(jobs.start(kb))
    run(jobs.start(kb))
    assert seen == [None, 'saved note']


def test_snapshot_change_during_model_call_prevents_publish(monkeypatch):
    kb, files = make_kb()
    db.update_kb_global_summary(kb, '旧摘要', 'old', 1)
    def chat(*a, **kw):
        with db.get_cursor() as cur:
            cur.execute("UPDATE knowledge_files SET content_hash='new' WHERE id=?", (files[0],))
        return '新摘要' * 40, 'fake'
    monkeypatch.setattr(summary.llm_client, 'get_client', lambda: SimpleNamespace(chat=chat))
    assert run(jobs.start(kb))['state'] == 'failed'
    assert db.get_kb_global_summary(kb)['summary'] == '旧摘要'


def test_cancellation_during_model_call_prevents_publish(monkeypatch):
    kb, _ = make_kb()
    job = jobs.start(kb)
    def chat(*a, **kw):
        jobs.cancel(job['id'], kb)
        return '新摘要' * 40, 'fake'
    monkeypatch.setattr(summary.llm_client, 'get_client', lambda: SimpleNamespace(chat=chat))
    assert run(job)['state'] == 'cancelled'
    assert db.get_kb_global_summary(kb) is None


def test_metadata_failure_never_reports_success(monkeypatch):
    from src.knowledge import summary_publication
    kb, files = make_kb(summaries=False)
    calls = []
    monkeypatch.setattr(summary_publication.vector_store, 'upsert_chunks', lambda **kw: calls.append(kw))
    monkeypatch.setattr(summary_publication.vector_store, 'delete_chunks', lambda *a, **kw: None)
    def fail(**kw):
        raise RuntimeError('disk unavailable')
    monkeypatch.setattr(db, 'upsert_document_meta', fail)
    with pytest.raises(RuntimeError):
        summary_publication.publish(files[0], kb, 'h0', dict(id='summary-test', text='new', embedding=[.1], metadata={}), 'jobs-col', 'fake', 1, [], lambda: None)
    assert calls == []
    assert not db.list_document_meta_by_kb(kb)[0]['summary']


def test_vector_failure_rolls_back_metadata(monkeypatch):
    from src.knowledge import summary_publication
    kb, files = make_kb(summaries=False)
    def fail(**kw):
        raise RuntimeError('vector unavailable')
    monkeypatch.setattr(summary_publication.vector_store, 'upsert_chunks', fail)
    monkeypatch.setattr(summary_publication.vector_store, 'delete_chunks', lambda *a, **kw: None)
    with pytest.raises(RuntimeError):
        summary_publication.publish(files[0], kb, 'h0', dict(id='summary-test', text='new', embedding=[.1], metadata={}), 'jobs-col', 'fake', 1, [], lambda: None)
    assert not db.list_document_meta_by_kb(kb)[0]['summary']


def test_recover_marks_interrupted_without_model_calls(monkeypatch):
    from src.knowledge import summary_publication
    kb, _ = make_kb()
    job = jobs.start(kb)
    monkeypatch.setattr(summary_publication, 'recover', lambda: None)
    jobs.recover()
    assert jobs.get(job['id'], kb)['state'] == 'interrupted'


def test_configured_timeout_reaches_network(monkeypatch):
    from src.core import api_retry
    from src.core.config import settings
    monkeypatch.setitem(settings.config, 'llm', {'request_timeout_seconds': 120})
    received = []
    api_retry.call_sync(lambda timeout: received.append(timeout.read), api_retry.RetryBudget.for_scene('summarize'), 'summarize')
    assert received == [120]


def test_existing_reading_index_is_preferred(monkeypatch):
    import sqlite3
    from contextlib import contextmanager
    from src.knowledge import reading_index
    kb, _ = make_kb(summaries=False)
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE sections(id INTEGER,label TEXT,text TEXT)')
    conn.execute("INSERT INTO sections VALUES(1,'章节','没有切片重叠的原文')")
    @contextmanager
    def index(file):
        yield conn
    monkeypatch.setattr(reading_index, 'open_index', index)
    monkeypatch.setattr(summary, '_fetch_chunks_by_ids', lambda *a: pytest.fail('must reuse reading index'))
    def summarize(**kw):
        assert [c.text for c in kw['chunks']] == ['没有切片重叠的原文']
        return summary.SummarizeResult(ok=True, summary='文档摘要')
    monkeypatch.setattr(summary, 'summarize_and_extract', summarize)
    monkeypatch.setattr(summary.llm_client, 'get_client', lambda: Client())
    try:
        assert run(jobs.start(kb))['state'] == 'done'
    finally:
        conn.close()


def test_preview_is_local_and_large_document_defaults_to_overview(monkeypatch):
    from src.knowledge import summary_plan
    kb, files = make_kb(summaries=False)
    monkeypatch.setattr(summary_plan, 'read_chunks', lambda *a: [Chunk('大文档' * 150000, {})])
    monkeypatch.setattr(summary.llm_client, 'get_client', lambda: pytest.fail('preview must not call AI'))
    plan = summary_plan.preview(kb)
    assert plan['documents'][0]['action'] == 'overview'
    assert plan['documents'][0]['estimated_calls'] == 1
    assert plan['estimated_calls'] == 2
    full = summary_plan.preview(kb, files)
    assert full['documents'][0]['action'] == 'full'
    assert full['estimated_calls'] > plan['estimated_calls']
    with pytest.raises(Exception, match='预计调用量超过预算'):
        jobs.start(kb, full['token'], files, 1)


def test_preview_stale_token_rejected(monkeypatch):
    from src.knowledge import summary_plan
    kb, files = make_kb()
    plan = summary_plan.preview(kb)
    with db.get_cursor() as cur:
        cur.execute("UPDATE knowledge_files SET content_hash='new' WHERE id=?", (files[0],))
    monkeypatch.setattr(summary_plan, 'read_chunks', lambda *a: [Chunk('原文', {})])
    with pytest.raises(Exception, match='重新预览'):
        jobs.start(kb, plan['token'])


def test_overview_cache_is_separate_and_reused(monkeypatch):
    from src.knowledge import summary_plan
    kb, files = make_kb(summaries=False)
    monkeypatch.setattr(summary_plan, 'read_chunks', lambda *a: [Chunk('正文' * 200000, {})])
    monkeypatch.setattr(summary_plan, 'structural_stats', lambda *a: '本地统计')
    client = Client()
    monkeypatch.setattr(summary.llm_client, 'get_client', lambda: client)
    monkeypatch.setattr(summary, 'summarize_and_extract', lambda **kw: pytest.fail('large doc must use overview'))
    plan = summary_plan.preview(kb)
    assert run(jobs.start(kb, plan['token']))['state'] == 'done'
    assert len(client.inputs) == 2
    assert not db.list_document_meta_by_kb(kb)[0]['summary']
    assert '未覆盖全部正文' in db.get_kb_global_summary(kb)['summary']
    assert summary_plan.preview(kb)['documents'][0]['action'] == 'reuse_overview'
    assert run(jobs.start(kb))['reused'] == 1
    assert len(client.inputs) == 3
    file = db.list_files_in_kb(kb)[0]
    file['content_hash'] = 'different'
    assert summary_plan.cached_overview(file) is None


def test_complete_summary_wins_over_overview():
    from src.knowledge import summary_plan
    kb, files = make_kb()
    with db.get_cursor() as cur:
        cur.execute('INSERT INTO summary_overviews VALUES (?,?,?,?,?,?)', (files[0], 'h0', summary_plan.VERSION, '概览', '{}', 'fake'))
    assert summary_plan.preview(kb)['documents'][0]['action'] == 'reuse'


def test_overview_samples_beginning_middle_and_end():
    from src.knowledge import summary_plan
    text = 'BEGIN' + 'a' * 50000 + 'END'
    coverage, evidence = summary_plan.overview_evidence([Chunk(text, {})])
    assert 'BEGIN' in evidence and 'END' in evidence
    assert coverage['sampled_chars'] <= 18000
    assert coverage['sampled_chars'] < coverage['total_chars']
    assert coverage['windows'] == 12


def test_xml_stats_distinguish_text_nodes_from_business_records():
    from src.knowledge import summary_plan
    stats = json.loads(summary_plan.structural_stats({'file_type': 'xml'}, [Chunk('[name] A\n[name] B\n[value] 1', {})]))
    assert dict(stats['text_node_counts']) == {'name': 2, 'value': 1}
    assert '不是原始XML节点或业务记录总数' in stats['note']


def test_budget_stops_before_extra_call_and_preserves_old_summary(monkeypatch):
    from src.knowledge import summary_plan
    kb, _ = make_kb()
    db.update_kb_global_summary(kb, '旧摘要', 'fake', 1)
    plan = summary_plan.preview(kb)
    job = jobs.start(kb, plan['token'], budget=1)
    calls = []
    def chat(*a, **kw):
        calls.append(1)
        return ChatText('partial', stop_reason='max_tokens'), 'fake'
    monkeypatch.setattr(summary.llm_client, 'get_client', lambda: SimpleNamespace(chat=chat))
    result = run(job)
    assert result['state'] == 'failed'
    assert len(calls) == 1
    assert '调用预算' in result['detail']
    assert db.get_kb_global_summary(kb)['summary'] == '旧摘要'


def test_full_selection_promotes_overview_without_regenerating_other_docs(monkeypatch):
    from src.knowledge import summary_plan
    kb, files = make_kb(summaries=False)
    with db.get_cursor() as cur:
        cur.execute('INSERT INTO summary_overviews VALUES (?,?,?,?,?,?)', (files[0], 'h0', summary_plan.VERSION, '概览', '{}', 'fake'))
    monkeypatch.setattr(summary_plan, 'read_chunks', lambda *a: [Chunk('全文', {})])
    seen = []
    def generate(**kw):
        seen.append(kw['file_id'])
        return summary.SummarizeResult(ok=True, summary='完整摘要')
    monkeypatch.setattr(summary, 'summarize_and_extract', generate)
    monkeypatch.setattr(summary.llm_client, 'get_client', lambda: Client())
    plan = summary_plan.preview(kb, files)
    assert run(jobs.start(kb, plan['token'], files))['state'] == 'done'
    assert seen == files
    assert '【覆盖说明】' not in db.get_kb_global_summary(kb)['summary']
