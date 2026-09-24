"""Enhanced path isolation and evidence contracts; no live model/data access."""
import copy
from types import SimpleNamespace

import pytest

from src.core.config import settings
from src.core.deep_ai_options import DeepAiOptions
from src.qa import enhanced_ai as ai, rag, conversation_context, tool_agent
from src.qa.trace import TraceCollector, PipelineCancelled


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(settings, '_config', {'qa': {'top_k': 5, 'rerank': {'enabled': False},
        'conversation_context': {'context_window_tokens': 16384}}})
    token = conversation_context.active.set(None)
    yield
    conversation_context.active.reset(token)


def test_followup_keeps_user_context_not_assistant_claims():
    history = [{'role': 'user', 'content': '[历史轮次 t1]\n沪市 NoBidLevel 是什么？'},
               {'role': 'assistant', 'content': '错误的历史推断'}]
    queries = ai.history_queries('那深圳呢？', history)
    assert 'NoBidLevel' in queries[0] and '那深圳呢' in queries[0]
    assert '错误的历史推断' not in queries[0]
    assert not ai.history_queries('安装 ADT 失败可能是什么原因？', history)


@pytest.mark.parametrize('policy', ['evidence', 'balanced', 'exploratory'])
def test_policy_and_no_hit_does_not_claim_empty_kb(monkeypatch, policy):
    monkeypatch.setattr(ai, 'recall', lambda *a, **kw: ([], 'embed'))
    messages, hits, _ = ai.prepare('为什么失败', None, [], TraceCollector(), options=DeepAiOptions(constraint_strategy=policy))
    assert ai.POLICIES[policy] in messages[0]['content']
    assert not hits
    assert '不表示知识库为空' in messages[-1]['content']
    assert '请进入「知识库」页面上传' not in str(messages)


def test_budget_preserves_question_and_matches_citation_payload():
    question = 'NoBidLevel <说明> 必须保留'
    hits = [{'id': str(i), 'text': ('原文' * 10000), 'title': '文档'} for i in range(20)]
    history = [{'role': role, 'content': '历史' * 2000} for role in ['user', 'assistant'] * 10]
    messages, selected = ai.assemble(question, hits, history, '背景' * 10000, 'balanced', '', TraceCollector())
    size = sum(conversation_context.estimate(m['content']) + 16 for m in messages)
    assert size + ai.chat_options()['max_tokens'] + 512 <= 16384
    assert 'NoBidLevel &lt;说明&gt; 必须保留' in messages[-1]['content']
    assert len(selected) == 20
    assert '节选' in selected[0]['text']
    for i, h in enumerate(selected, 1):
        assert f'<chunk id="{i}"' in messages[-1]['content']
        assert h['text'] in messages[-1]['content']
    assert hits[0]['text'] == '原文' * 10000


def test_oversize_question_fails_explicitly():
    with pytest.raises(ValueError, match='超出'):
        ai.assemble('大问题' * 20000, [], [], '', 'balanced', '', TraceCollector())


def test_relevance_keeps_configuration_ahead_of_unrelated_sources():
    hits = [{'id': 'noise', 'text': 'AMA 银行间版本历史', 'title': 'AMA'},
            {'id': 'config', 'text': 'test_tool 使用配置示例 ```json SecurityCode Subscribe', 'title': '配置说明'},
            {'id': 'other', 'text': 'test tool 启动命令 README', 'title': '工具'}]
    ranked = ai.ranked_unique('使用ama的testtool工具怎么接收行情', hits, 3)
    assert {h['id'] for h in ranked[:2]} == {'config', 'other'}
    assert ranked[-1]['id'] == 'noise'
    assert ai.normalized('test_tool') == ai.normalized('testtool')
    assert ai.normalized('Data-Reader') == ai.normalized('data reader')


def test_supplement_reads_only_authorized_document_ids(monkeypatch):
    monkeypatch.setattr(ai.metadata_db, 'get_kb', lambda kb: {'collection_name': 'allowed'})
    monkeypatch.setattr(ai.metadata_db, 'list_files_in_kb', lambda kb, status: [{'id': 3, 'chunk_ids_json': '["tail", "config"]'}])
    seen = []
    def fetch(ids, collection_name):
        seen.append((ids, collection_name))
        return {'config': {'id': 'config', 'file_id': 3, 'text': 'test_tool 配置 Subscribe SecurityCode 示例'}}
    monkeypatch.setattr(ai.retrieval.vector_store, 'get_chunks_by_ids', fetch)
    hits = [{'id': 'tail', 'file_id': 3, 'text': 'test_tool 排障结尾'},
            {'id': 'foreign', 'file_id': 999, 'text': '别的库'}]
    result = ai.supplement('testtool 怎么配置', hits, 'kb', TraceCollector())
    assert result[0]['id'] == 'config'
    assert seen == [(['tail', 'config'], 'allowed')]


def test_incomplete_rerank_preserves_baseline(monkeypatch, tmp_path):
    settings._config['qa']['enhanced_ai'] = {'rerank_enabled': True, 'rerank_seconds': 1}
    monkeypatch.setattr(ai.retrieval.reranker, '_onnx_model_dir', lambda: tmp_path)
    model = tmp_path / 'onnx' / 'model_int8.onnx'; model.parent.mkdir(); model.touch()
    hits = [{'id': str(i), 'text': f'doc {i}'} for i in range(8)]
    baseline = ai.ranked_unique('doc', hits, 80)
    monkeypatch.setattr(ai.retrieval.reranker, 'rerank', lambda q, rows, **kw: list(reversed(rows)))
    clock = iter([0, 2])
    monkeypatch.setattr(ai.time, 'monotonic', lambda: next(clock))
    assert ai.rerank_complete('doc', hits, TraceCollector()) == baseline


def test_default_rerank_never_loads_model(monkeypatch):
    monkeypatch.setattr(ai.retrieval.reranker, '_onnx_model_dir', lambda: pytest.fail('must not load model'))
    hits = [{'id': str(i), 'text': f'doc {i}'} for i in range(3)]
    assert len(ai.rerank_complete('doc', hits, TraceCollector())) == 3


@pytest.mark.asyncio
async def test_deep_dispatch_is_unchanged_and_never_enters_enhanced(monkeypatch):
    monkeypatch.setattr(ai, 'prepare', lambda *a, **k: pytest.fail('deep must not enter enhanced'))
    captured = {}
    async def deep(question, history, kb, **kwargs):
        captured.update(kwargs)
        yield {'type': 'token', 'data': {'text': '直接输出'}}
        yield {'type': 'done', 'data': {'answer': '直接输出'}}
    monkeypatch.setattr(tool_agent, 'ask_stream', deep)
    options = DeepAiOptions(max_rounds=None, constraint_strategy='exploratory')
    events = [e async for e in rag.ask_stream('问题', [], 'kb', mode='deep_ai', top_k=7,
        session_id='session', turn_id='turn', api_retry_count=3, deep_ai_options=options)]
    assert events[0]['type'] == 'token'
    assert captured == dict(top_k=7, session_id='session', turn_id='turn', strict_knowledge=False,
                            api_retry_count=3, deep_ai_options=options)
    assert captured['deep_ai_options'] is options


@pytest.mark.asyncio
@pytest.mark.parametrize('fail', [False, True])
async def test_enhanced_empty_evidence_streams_and_preserves_partial(monkeypatch, fail):
    class Client:
        def is_embedding_warm(self): return True
        async def chat_stream(self, messages, **kwargs):
            assert kwargs['max_tokens'] == 4096
            yield '分析', 'test'
            if fail: raise RuntimeError('connection closed')
            yield '建议', 'test'
    monkeypatch.setattr(rag.llm_client, 'get_client', lambda: Client())
    monkeypatch.setattr(ai, 'prepare', lambda *a, **k: ([{'role': 'user', 'content': '没有命中'}], [], 'embed'))
    monkeypatch.setattr(rag, '_run_pipeline', lambda *a, **kw: pytest.fail('old pipeline used'))
    events = [e async for e in rag.ask_stream('问题', mode='ai')]
    assert next(e for e in events if e['type'] == 'sources')['data'] == []
    assert next(e for e in events if e['type'] == 'token')['data']['text'] == '分析'
    assert events[-1]['type'] == ('error' if fail else 'done')
    assert events[-1]['data']['partial' if fail else 'answer'] == ('分析' if fail else '分析建议')


def test_sync_enhanced_uses_same_prepare_and_options(monkeypatch):
    calls = {}
    def prepare(*args, **kwargs):
        calls.update(kwargs)
        return [{'role': 'user', 'content': '问题'}], [], 'embed'
    monkeypatch.setattr(ai, 'prepare', prepare)
    monkeypatch.setattr(rag.llm_client, 'get_client', lambda: SimpleNamespace(chat=lambda *a, **kw: ('通用分析', 'test')))
    options = DeepAiOptions(constraint_strategy='evidence')
    result = rag.ask('问题', mode='ai', deep_ai_options=options)
    assert result.answer == '通用分析' and calls['options'] is options


def test_cancellation_not_downgraded_to_empty_evidence(monkeypatch):
    def cancelled(*a, **kw): raise PipelineCancelled('stopped')
    monkeypatch.setattr(ai, 'recall', cancelled)
    with pytest.raises(PipelineCancelled):
        ai.prepare('问题', 5, [], TraceCollector())


def test_document_affinity_cannot_fill_every_slot():
    hits = [{'id': 'tool', 'file_id': 1, 'text': 'test_tool 配置示例 ' + '说明' * 60}]
    hits += [{'id': f'noise{i}', 'file_id': 1, 'text': f'网络参数{i} ' + '网络' * 100} for i in range(12)]
    hits += [{'id': 'target', 'file_id': 2, 'text': '600570 行情订阅 示例代码 ' + '市场定义' * 50}]
    selected = ai.ranked_unique('testtool 怎么接收600570行情', hits, 3)
    assert {'tool', 'target'} <= {h['id'] for h in selected}


def test_overlapping_windows_merge_without_mixing_versions():
    common = '共享原文段落' * 40
    a = {'id': 'a', 'file_id': 1, 'content_hash': 'v1', 'section_index': 0, 'text': '起始' + common}
    b = {**a, 'id': 'b', 'text': common + '末尾'}
    c = {**b, 'id': 'c', 'content_hash': 'v2'}
    merged = ai.merge_windows([a, b, c])
    assert len(merged) == 2
    assert merged[0]['text'] == '起始' + common + '末尾'


def test_supplement_follows_hit_near_end_not_document_prefix(monkeypatch):
    monkeypatch.setattr(ai.metadata_db, 'get_kb', lambda kb: {'collection_name': 'allowed'})
    import json
    ids = [str(i) for i in range(150)]
    monkeypatch.setattr(ai.metadata_db, 'list_files_in_kb', lambda *a, **kw: [{'id': 1, 'chunk_ids_json': json.dumps(ids)}])
    seen = []
    monkeypatch.setattr(ai.retrieval.vector_store, 'get_chunks_by_ids', lambda ids, **kw: seen.extend(ids) or {})
    ai.supplement('接收行情', [{'id': '140', 'file_id': 1, 'text': '匹配正文'}], 'kb', TraceCollector())
    assert seen == ['138', '139', '140', '141', '142']
