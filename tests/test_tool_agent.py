"""Isolated agent and native protocol tests. No paid model or real user data."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from src.core import accounts
from src.core.config import settings
from src.core.llm_providers import OpenAIProvider, AnthropicProvider, AnthropicArchProvider
from src.core.tool_chat import ToolChat, ToolCall, ToolTurn, ToolProtocolError
from src.qa import knowledge_tools as kt, tool_agent, rag
from src.api.routes_sessions import _trim_sources


@pytest.fixture
def knowledge(monkeypatch):
    monkeypatch.setattr(accounts, 'enabled', False)
    monkeypatch.setattr(settings, '_config', {'qa': {'deep_ai': {'semantic_verification_enabled': False}}})
    files = [dict(id=1, relative_path='adt/install.md', content_hash='v1', status='done',
                  file_type='md', chunk_ids_json='["a", "b"]')]
    chunks = {
        'a': dict(id='a', text='安装 ADT 需要可用的依赖。' + '上下文' * 2100, section_index=0, chunk_index=0, section_label='安装'),
        'b': dict(id='b', text='失败时检查依赖版本和安装日志。', section_index=1, chunk_index=0, section_label='排障'),
    }
    monkeypatch.setattr(kt.db, 'list_files_in_kb', lambda kb: files if kb == 'kb' else [])
    monkeypatch.setattr(kt.db, 'get_kb', lambda kb: dict(name='ADT', description='安装文档', collection_name='kb-index'))
    monkeypatch.setattr(kt.db, 'get_kb_global_summary', lambda kb: None)
    def get(ids, **kwargs):
        live = [chunks[i] for i in ids if i in chunks]
        return dict(ids=[h['id'] for h in live], documents=[h['text'] for h in live],
                    metadatas=[{k: v for k, v in h.items() if k not in ('id', 'text')} for h in live])
    monkeypatch.setattr(kt.vector_store, 'get_chroma_client', lambda: SimpleNamespace(get_collection=lambda name: SimpleNamespace(get=get)))
    monkeypatch.setattr(kt.retrieval, 'search', lambda *args, **kwargs: ([chunks['a']], 'embedding'))
    monkeypatch.setattr(tool_agent.ai_call_logger, 'log_call', lambda **kwargs: None)
    return files, chunks


def test_document_paging_snapshots_and_cross_scope(knowledge):
    kb = kt.KnowledgeTools('kb')
    outline = kb.execute('get_document_outline', {'document_id': 1})
    assert len(outline['sections']) == 2
    first = kb.execute('read_document', {'document_id': 1, 'version': 'v1'})
    assert first['next_offset'] == 6000
    second = kb.execute('read_document', {'document_id': 1, 'version': 'v1', 'offset': first['next_offset']})
    assert second['next_offset'] is None
    assert first['evidence'][0]['text'] + second['evidence'][0]['text'] == knowledge[1]['a']['text']
    assert first['evidence'][0]['citation'] == 1 and second['evidence'][0]['citation'] == 2
    assert kb.execute('read_document', {'document_id': 1, 'version': 'v1'})['cached']
    snapshot = _trim_sources(kb.sources(), long_content=True)[0]
    assert len(snapshot['content']) == 6000 and snapshot['content_hash'] == 'v1' and snapshot['chunk_ids'] == ['a']
    assert kt.KnowledgeTools('other').execute('read_document', {'document_id': 1, 'version': 'v1'})['error']


def test_read_pagination_preserves_whitespace_and_budget_failure(knowledge):
    files, chunks = knowledge
    chunks['a']['text'] = '\n  ' + 'x' * 5995 + '\n\n tail\n'
    kb = kt.KnowledgeTools('kb')
    first = kb.read(1, 'v1')
    second = kb.read(1, 'v1', offset=first['next_offset'])
    assert first['evidence'][0]['text'] + second['evidence'][0]['text'] == chunks['a']['text']
    assert first['evidence'][0]['reading']['offset_basis'] == 'section'
    assert not first['evidence'][0]['reading']['section_complete']
    small = kt.KnowledgeTools('kb', max_chars=3)
    page = small.read(1, 'v1')
    assert page['next_offset'] == 3
    assert page['evidence'][0]['reading']['budget_truncated']
    assert small.read(1, 'v1', offset=3)['error'] == 'evidence_budget'


def test_search_then_read_charges_only_new_section_coordinates(knowledge):
    kb = kt.KnowledgeTools('kb')
    found = kb.search('ADT', 1)
    ev = found['evidence'][0]
    assert ev['reading']['truncated'] and not ev['reading']['section_complete']
    assert ev['read_hint'] == dict(document_id=1, version='v1', section=0, offset=0)
    assert kb.used_chars == 2500
    kb.read(**dict(document_id=1, version='v1', section=0))
    assert kb.used_chars == 6000
    kb.read(1, 'v1', offset=2000)
    assert kb.used_chars == len(knowledge[1]['a']['text'])


def test_dedup_does_not_merge_independent_sources_or_versions(knowledge):
    files, chunks = knowledge
    kb = kt.KnowledgeTools('kb')
    first = kb.evidence(chunks['b'], files[0])
    duplicate = kb.evidence({**chunks['b'], 'id': 'duplicate'}, files[0])
    assert duplicate['citation'] == first['citation'] and duplicate['reused']
    independent = kb.evidence(chunks['b'], {**files[0], 'id': 2})
    new_version = kb.evidence(chunks['b'], {**files[0], 'content_hash': 'v2'})
    assert independent['citation'] != first['citation'] != new_version['citation']


def test_original_conditions_survive_search_rewrite(knowledge):
    kb = kt.KnowledgeTools('kb', question='Linux 版本 2 离线环境如何安装？')
    result = kb.execute('search_knowledge', {'query': 'ADT', 'document_id': 1})
    assert result['question_context']['original_question'] == kb.question
    assert 'rule' not in result['question_context']


def test_table_continuation_retains_header_and_literal_indices(knowledge):
    files, chunks = knowledge
    chunks['a']['text'] = '| 平台 | 值 |\n| --- | --- |\n' + '| Linux | `[0]` |\n' * 500
    kb = kt.KnowledgeTools('kb')
    first = kb.read(1, 'v1')
    second = kb.read(1, 'v1', offset=first['next_offset'])
    assert second['context_header'] == '| 平台 | 值 |\n| --- | --- |'
    assert '[0]' in second['evidence'][0]['text']


def test_progress_ignores_renumbering_order_and_overlapping_reads():
    from src.qa.evidence_state import Progress
    progress = Progress()
    a = dict(citation=1, document_id=1, version='v1', section=0, text='abc',
             reading=dict(offset_basis='section', start=0, end=3))
    assert progress.observe({'evidence': [a]})['new_evidence'] == 1
    assert progress.observe({'evidence': [{**a, 'citation': 99, 'matched_fields': ['abc']}]})['new_evidence'] == 0
    assert progress.observe({'evidence': [{**a, 'text': 'bc', 'reading': dict(offset_basis='section', start=1, end=3)}]})['duplicates'] == 1
    assert progress.observe({'evidence': [{**a, 'document_id': 2}]})['new_evidence'] == 1
    docs = [{'document_id': 1}, {'document_id': 2}]
    assert progress.observe({'documents': docs})['new_navigation'] == 2
    assert progress.observe({'documents': list(reversed(docs))})['new_navigation'] == 0


def test_equal_read_pages_keep_distinct_continuation_coordinates(knowledge):
    knowledge[1]['a']['text'] = 'x' * 12000 + ' END'
    kb = kt.KnowledgeTools('kb')
    first = kb.read(1, 'v1')
    second = kb.read(1, 'v1', offset=6000)
    assert first['evidence'][0]['citation'] != second['evidence'][0]['citation']
    assert second['next_offset'] == 12000
    assert kb.used_chars == 12000


def test_evidence_metadata_survives_session_snapshot(knowledge):
    from src.api.routes_sessions import _trim_trace
    kb = kt.KnowledgeTools('kb')
    kb.search('ADT', 1)
    source = _trim_sources(kb.sources(), long_content=True)[0]
    assert source['reading']['truncated']
    trace = _trim_trace([dict(stage='evidence_summary', stop_reason='round_limit', tool_calls=10, evidence_chars=2500)])
    assert trace[0]['stop_reason'] == 'round_limit' and trace[0]['evidence_chars'] == 2500


def test_missing_index_chunk_is_not_labelled_complete(knowledge):
    knowledge[0][0]['chunk_ids_json'] = '["a", "b", "missing"]'
    result = kt.KnowledgeTools('kb').read(1, 'v1', section=1)
    assert result['incomplete']
    assert result['evidence'][0]['reading']['index_incomplete']
    assert not result['evidence'][0]['reading']['section_complete']


def quality_cases():
    from pathlib import Path
    return json.loads((Path(__file__).parent / 'fixtures/deep_ai_quality_cases.json').read_text('utf-8'))


@pytest.mark.parametrize('case', quality_cases(), ids=lambda case: case['id'])
def test_quality_fixture_evidence_preserves_conditions_and_source_boundaries(knowledge, case):
    # These assertions validate evidence plumbing, not generated answer quality.
    files, chunks = knowledge
    files.clear()
    chunks.clear()
    for i, doc in enumerate(case['documents'], 1):
        cid = str(i)
        files.append(dict(id=i, relative_path=doc['name'], content_hash='v1', status='done',
                          file_type='md', chunk_ids_json=json.dumps([cid])))
        chunks[cid] = dict(id=cid, text=doc['text'], section_index=0, chunk_index=0, section_label=doc['name'])
    kb = kt.KnowledgeTools('kb', question=case['question'])
    for i, doc in enumerate(case['documents'], 1):
        result = kb.execute('read_document', dict(document_id=i, version='v1'))
        ev = result['evidence'][0]
        assert ev['document_id'] == i and ev['text'] == doc['text']
        assert ev['reading']['section_complete']
        assert result['question_context']['original_question'] == case['question']
    assert len(kb.sources()) == len(case['documents'])


@pytest.mark.parametrize('name,args', [('delete_document', {}), ('read_document', {'document_id': 1, 'version': 'v1', 'kb_id': 'other'}),
                                       ('search_knowledge', '{broken'), ('read_document', {'document_id': True, 'version': 'v1'}),
                                       ('search_knowledge', {'query': ''}), ('search_knowledge', {'query': 'q', 'limit': 100})])
def test_untrusted_arguments_rejected(knowledge, name, args):
    assert kt.KnowledgeTools('kb').execute(name, args)['error']


def test_deleted_and_changed_evidence_not_reused(knowledge):
    files, chunks = knowledge
    kb = kt.KnowledgeTools('kb')
    assert kb.execute('search_knowledge', {'query': 'ADT'})['evidence']
    files[0]['content_hash'] = 'v2'
    assert kb.execute('read_document', {'document_id': 1, 'version': 'v1'})['error']
    with pytest.raises(ValueError):
        kb.sources()
    files.clear()
    with pytest.raises(ValueError):
        kb.sources()


def test_stale_lexical_hits_and_embedding_failure(knowledge, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('embed unavailable')
    monkeypatch.setattr(kt.retrieval, 'search', fail)
    monkeypatch.setattr(kt.bm25_index, 'query_enhanced', lambda *args: [{'id': 'deleted', 'text': 'bad'}, {'id': 'b', 'text': 'stale'}])
    result = kt.KnowledgeTools('kb').execute('search_knowledge', {'query': 'ADT'})
    assert len(result['evidence']) == 1
    assert result['evidence'][0]['text'] == knowledge[1]['b']['text']
    assert '语义检索暂不可用' in result['note']


def test_parsing_and_index_failure_are_not_no_match(knowledge, monkeypatch):
    files, _ = knowledge
    files[0]['status'] = 'failed'
    kb = kt.KnowledgeTools('kb')
    assert kb.execute('get_document_outline', {'document_id': 1})['error']
    files[0]['status'] = 'done'
    def fail():
        raise RuntimeError('storage offline')
    monkeypatch.setattr(kt.vector_store, 'get_chroma_client', fail)
    assert kb.execute('read_document', {'document_id': 1, 'version': 'v1'})['error'] == 'unavailable'


def test_permission_rechecked_on_cache(knowledge, monkeypatch):
    kb = kt.KnowledgeTools('kb')
    kb.execute('get_kb_overview', {})
    monkeypatch.setattr(accounts, 'enabled', True)
    monkeypatch.setattr(accounts, 'require_kb', lambda *args: (_ for _ in ()).throw(HTTPException(403, 'revoked')))
    with pytest.raises(HTTPException):
        kb.execute('get_kb_overview', {})


class Model:
    name = 'chosen-provider'
    provider = SimpleNamespace(chat_model='model')
    def __init__(self, calls=None):
        self.turns = iter(calls if calls is not None else [
            [ToolCall('1', 'search_knowledge', {'query': 'ADT 安装失败'})],
            [ToolCall('2', 'get_document_outline', {'document_id': 1})],
            [ToolCall('3', 'read_document', {'document_id': 1, 'version': 'v1', 'section': 1})], []])
        self.returned = []
        self.closed = False
    async def turn(self, *args, **kwargs):
        return ToolTurn('', next(self.turns), {})
    def results(self, value):
        self.returned.extend(value)
    async def final_stream(self, *args, **kwargs):
        for part in ['检查依赖版本和日志。【ci', 'te:2】 通用知识：确认网络。无效引用【cite:99', '】']:
            yield part
    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_stream_arrives_before_generation_finishes_even_with_legacy_verification_config(knowledge, monkeypatch):
    settings._config['qa']['deep_ai']['semantic_verification_enabled'] = True
    model = Model()
    release = asyncio.Event()
    calls = []
    async def final(*args, **kwargs):
        calls.append(1)
        for _ in range(32):
            yield '正文'
        await asyncio.wait_for(release.wait(), 2)
        yield '结束【cite:2】'
    model.final_stream = final
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *a, **k: model)
    events = []
    async for event in tool_agent.ask_stream('排障', [], 'kb'):
        events.append(event)
        if event['type'] == 'token':
            release.set()
    assert release.is_set()
    assert calls == [1]
    assert events[-1]['type'] == 'done'
    assert ''.join(e['data']['text'] for e in events if e['type'] == 'token') == events[-1]['data']['answer']
    assert not any(e['type'] == 'stage' and e['data']['stage'].startswith('answer_verification') for e in events)
    assert model.closed


@pytest.mark.asyncio
async def test_permission_revocation_before_stream_flush_blocks_content(knowledge, monkeypatch):
    model = Model()
    async def final(*args, **kwargs):
        def revoked(self):
            raise HTTPException(403, 'revoked')
        monkeypatch.setattr(kt.KnowledgeTools, 'check', revoked)
        yield '不可展示的正文'
    model.final_stream = final
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *a, **k: model)
    events = [e async for e in tool_agent.ask_stream('排障', [], 'kb')]
    assert events[-1]['type'] == 'error'
    assert not any(e['type'] == 'token' for e in events)
    assert events[-1]['data']['partial'] == ''
    assert model.closed


@pytest.mark.asyncio
async def test_user_stop_during_stream_closes_generation_and_model(knowledge, monkeypatch):
    model = Model()
    cancelled = asyncio.Event()
    async def final(*args, **kwargs):
        try:
            for _ in range(32):
                yield '正文'
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    model.final_stream = final
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *a, **k: model)
    stream = tool_agent.ask_stream('排障', [], 'kb')
    async for event in stream:
        if event['type'] == 'token':
            break
    await stream.aclose()
    assert cancelled.is_set() and model.closed


@pytest.mark.asyncio
@pytest.mark.parametrize('maximum,expected', [(None, 12), (10, 10), (20, 12)])
async def test_round_options_remove_old_six_round_cap(knowledge, monkeypatch, maximum, expected):
    model = Model([[ToolCall(str(i), 'list_documents', {'query': str(i)})] for i in range(12)] + [[]])
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *a, **k: model)
    original = kt.KnowledgeTools.execute
    def execute(self, name, args):
        if name == 'list_documents':
            return {'documents': [{'document_id': args['query']}]}
        return original(self, name, args)
    monkeypatch.setattr(kt.KnowledgeTools, 'execute', execute)
    events = [e async for e in tool_agent.ask_stream('q', [], 'kb', deep_ai_options={'max_rounds': maximum})]
    assert events[-1]['type'] == 'done'
    assert len(model.returned) == expected
    assert any('总时长：不限' in e.get('data', {}).get('notes', '') for e in events)


@pytest.mark.asyncio
async def test_empty_session_has_no_history_tools_or_instructions(knowledge, monkeypatch):
    from src.qa import conversation_context as cc
    context = cc.Context('new-session', None, 'kb')
    cc.assemble(context, 'q', {}, None, cc.config())
    assert context.background == '' and context.messages == []
    model = Model()
    seen = []
    original = model.turn
    async def turn(tools, **kwargs):
        seen.append([t['name'] for t in tools])
        return await original(tools, **kwargs)
    model.turn = turn
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *a, **k: model)
    token = cc.active.set(context)
    try:
        events = [e async for e in tool_agent.ask_stream('q', [], 'kb')]
    finally:
        cc.active.reset(token)
    assert events[-1]['type'] == 'done'
    assert all(len(names) == len(kt.TOOLS)+1 and 'search_conversation_history' not in names for names in seen)


def test_deep_options_defaults_and_validation():
    from pydantic import ValidationError
    from src.core.deep_ai_options import DeepAiOptions
    options = DeepAiOptions()
    assert options.max_rounds == 10
    assert options.total_timeout_seconds == 360 and not options.time_limit_enabled
    assert DeepAiOptions(max_rounds=None).max_rounds is None
    assert DeepAiOptions(max_rounds=10000).max_rounds == 10000
    for value in (0, -1, True, 1.5):
        with pytest.raises(ValidationError):
            DeepAiOptions(max_rounds=value)


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled,expected_calls', [(True, 1), (False, 3)])
async def test_total_timer_can_be_disabled_without_disabling_call_deadlines(knowledge, monkeypatch, enabled, expected_calls):
    clock = [1000.0]
    monkeypatch.setattr(tool_agent, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    model = Model()
    original = model.results
    def results(value):
        original(value)
        clock[0] += 301
    model.results = results
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *a, **k: model)
    events = [e async for e in tool_agent.ask_stream('q', [], 'kb', deep_ai_options={
        'time_limit_enabled': enabled, 'total_timeout_seconds': 360})]
    assert events[-1]['type'] == 'done'
    assert len(model.returned) == expected_calls
    assert model.deadline <= clock[0] + 180


@pytest.mark.asyncio
async def test_unlimited_rounds_notice_does_not_force_stop(knowledge, monkeypatch):
    model = Model([[ToolCall(str(i), 'list_documents', {'query': 'missing' + str(i)})] for i in range(8)] + [[]])
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *a, **k: model)
    events = [e async for e in tool_agent.ask_stream('q', [], 'kb', deep_ai_options={'max_rounds': None})]
    assert events[-1]['type'] == 'done'
    assert len(model.returned) == 8
    assert any('近期调用' in e['data'].get('notes', '') for e in events if e['type'] == 'stage')


def test_field_search_exact_names_scope_and_coverage(knowledge):
    files, chunks = knowledge
    chunks.clear()
    chunks.update({
        'generic': dict(id='generic', text='沪深 快照 买卖 档位 报价 展示', section_index=0),
        'exact': dict(id='exact', text='NoBidLevel 委买报价展示档位数', section_index=1),
        'near': dict(id='near', text='NoBidLevelOther 其他字段', section_index=2),
        'wrong': dict(id='wrong', text='MDPriceLevel 档位', section_index=3, section_label='银行间暨外汇'),
    })
    files[0]['chunk_ids_json'] = json.dumps(list(chunks))
    result = kt.KnowledgeTools('kb').search('沪深 快照 买卖 档位 报价 展示 NoBidLevel NoOfferLevel MDPriceLevel', 1)
    assert result['evidence'][0]['text'].startswith('NoBidLevel ')
    assert result['matched_fields'] == ['MDPriceLevel', 'NoBidLevel']
    assert result['missing_fields'] == ['NoOfferLevel']
    assert not any(ev.get('scope_warning') for ev in result['evidence'])


def test_legacy_chunks_without_sections_are_not_collapsed():
    from src.qa import field_search
    chunks = [{'id': str(i), 'text': f'field {i}', 'section_index': None, 'section_label': ''} for i in range(5)]
    assert len(field_search.rank('field', chunks, 5, lambda text: set(text.split()))) == 5


@pytest.mark.asyncio
async def test_final_answer_reports_fields_not_found_in_actual_evidence(knowledge, monkeypatch):
    model = Model()
    instructions = []
    original = model.final_stream
    async def final(tools, instruction, **kwargs):
        instructions.append(instruction)
        async for token in original(tools, instruction, **kwargs):
            yield token
    model.final_stream = final
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *a, **k: model)
    events = [e async for e in tool_agent.ask_stream('NoBidLevel 如何映射', [], 'kb')]
    assert events[-1]['type'] == 'done'
    assert 'NoBidLevel 如何映射' in instructions[0]
    assert '字段检索范围' not in instructions[0]


@pytest.mark.asyncio
async def test_live_deep_entry_multi_hop_streams_and_preserves_history_boundary(knowledge, monkeypatch):
    model = Model()
    prompts = []
    def create(system, messages, **kwargs):
        prompts.append((system, messages))
        return model
    monkeypatch.setattr(tool_agent, 'create_tool_chat', create)
    events = [e async for e in rag.ask_stream('安装 ADT 失败', [{'role': 'assistant', 'content': 'invented fact'}, {'role': 'user', 'content': 'Windows 11'}], 'kb', mode='deep_ai', strict_knowledge=True)]
    result = events[-1]['data']
    assert events[-1]['type'] == 'done' and result['mode'] == 'deep_ai'
    assert result['used_provider'] == 'chosen-provider' and model.closed
    assert '【cite:2】' in result['answer'] and '【cite:99】' not in result['answer']
    assert result['sources'][1]['content'] == knowledge[1]['b']['text']
    assert 'AI约束策略：资料优先' in prompts[0][0]
    assert 'invented fact' not in str(prompts) and 'Windows 11' in str(prompts)
    assert any(e['type'] == 'token' for e in events)
    assert len(model.returned) == 3


@pytest.mark.asyncio
async def test_direct_answer_does_not_require_tool_call(knowledge, monkeypatch):
    model = Model([[]])
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *args, **kwargs: model)
    events = [e async for e in rag.ask_stream('q', kb_scope='kb', mode='deep_ai')]
    assert events[-1]['type'] == 'done' and model.closed


@pytest.mark.asyncio
async def test_repeated_tools_are_bounded(knowledge, monkeypatch):
    model = Model([[ToolCall(str(i), 'search_knowledge', {'query': 'ADT'})] for i in range(10)])
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *args, **kwargs: model)
    events = [e async for e in rag.ask_stream('q', kb_scope='kb', mode='deep_ai')]
    assert events[-1]['type'] == 'done' and len(model.returned) == 6


@pytest.mark.asyncio
async def test_stop_cancels_model_and_closes_transport(knowledge, monkeypatch):
    model = Model()
    started = asyncio.Event()
    async def wait(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    model.turn = wait
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *args, **kwargs: model)
    stream = rag.ask_stream('q', kb_scope='kb', mode='deep_ai')
    assert (await anext(stream))['type'] == 'stage'
    await asyncio.wait_for(started.wait(), 3)
    await stream.aclose()
    # Delegating async generators must close the inner agent as well.
    await asyncio.sleep(0)
    assert model.closed


def wire_response(style, *, calls=True):
    if style == 'chat':
        return {'choices': [{'finish_reason': 'tool_calls' if calls else 'stop', 'message': {'role': 'assistant', 'content': '', 'reasoning_content': 'private',
                  'tool_calls': [{'id': 'call1', 'type': 'function', 'function': {'name': 'get_kb_overview', 'arguments': '{}'}}] if calls else []}}]}
    if style == 'responses':
        return {'status': 'completed', 'output': [{'type': 'reasoning', 'id': 'rs1', 'encrypted_content': 'opaque'},
                    {'type': 'function_call', 'call_id': 'call1', 'name': 'get_kb_overview', 'arguments': '{}'}] if calls else []}
    return {'stop_reason': 'tool_use' if calls else 'end_turn', 'content': [{'type': 'thinking', 'thinking': 'private', 'signature': 'opaque'},
                     {'type': 'tool_use', 'id': 'call1', 'name': 'get_kb_overview', 'input': {}}] if calls else []}


@pytest.mark.asyncio
@pytest.mark.parametrize('style', ['chat', 'responses', 'anthropic', 'arch'])
async def test_native_protocol_round_trip_and_final_stream(knowledge, style):
    cls = {'chat': OpenAIProvider, 'responses': OpenAIProvider, 'anthropic': AnthropicProvider, 'arch': AnthropicArchProvider}[style]
    base = 'https://example.test' if style == 'chat' else 'https://example.test/v1' + ('/responses' if style == 'responses' else '')
    provider = cls('test', {'base_url': base, 'chat_model': 'test'}, 'secret')
    requests = []
    def handler(request):
        expected_path = '/v1/chat/completions' if style == 'chat' else '/v1/responses' if style == 'responses' else '/v1/messages'
        assert request.url.path == expected_path
        payload = json.loads(request.content)
        requests.append(payload)
        assert 'tools' in payload
        if not payload.get('stream'):
            return httpx.Response(200, json=wire_response(style))
        if style == 'chat':
            events = [{'choices': [{'delta': {'content': 'answer'}, 'finish_reason': None}]}, {'choices': [{'delta': {}, 'finish_reason': 'stop'}]}]
        elif style == 'responses':
            events = [{'type': 'response.output_text.delta', 'delta': 'answer'}, {'type': 'response.completed'}]
        else:
            events = [{'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'answer'}}, {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}}]
        return httpx.Response(200, text=''.join('data: ' + json.dumps(e) + '\n\n' for e in events))
    model = ToolChat(provider, 'system', [{'role': 'user', 'content': 'q'}], transport=httpx.MockTransport(handler))
    try:
        turn = await model.turn(kt.TOOLS, force=True)
        assert turn.calls[0].id == 'call1'
        model.results([(turn.calls[0], {'name': 'kb'})])
        assert ''.join([s async for s in model.final_stream(kt.TOOLS, 'answer now')]) == 'answer'
        final = requests[-1]
        history = final.get('messages', final.get('input'))
        assert 'call1' in json.dumps(history) and 'opaque' in json.dumps(history) if style != 'chat' else 'reasoning_content' in json.dumps(history)
        if style == 'arch':
            assert 'metadata' in final
    finally:
        await model.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('style', ['chat', 'responses', 'anthropic', 'arch'])
async def test_agent_synthesis_preserves_native_protocol_conversation(knowledge, monkeypatch, style):
    cls = {'chat': OpenAIProvider, 'responses': OpenAIProvider, 'anthropic': AnthropicProvider, 'arch': AnthropicArchProvider}[style]
    base = 'https://example.test/v1' + ('/responses' if style == 'responses' else '')
    provider = cls('test', {'base_url': base, 'chat_model': 'test'}, 'secret')
    captured = []
    def handler(request):
        payload = json.loads(request.content)
        captured.append(payload)
        if len(captured) == 1:
            wire = wire_response(style)
            if style == 'responses':
                events = [{'type':'response.completed','response':wire}]
            elif style == 'chat':
                msg = wire['choices'][0]['message']
                for i,c in enumerate(msg['tool_calls']): c['index']=i
                events = [{'choices':[{'delta':msg,'finish_reason':'tool_calls'}]}]
            else:
                events = [{'type':'content_block_start','index':i,'content_block':b} for i,b in enumerate(wire['content'])]
                events.append({'type':'message_delta','delta':{'stop_reason':'tool_use'}})
            return httpx.Response(200,text=''.join('data: '+json.dumps(e)+'\n\n' for e in events))
        if style == 'chat':
            events = [{'choices': [{'delta': {'content': 'answer'}, 'finish_reason': 'stop'}]}]
        elif style == 'responses':
            events = [{'type': 'response.output_text.delta', 'delta': 'answer'}, {'type': 'response.completed'}]
        else:
            events = [{'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'answer'}},
                      {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}}]
        return httpx.Response(200, text=''.join('data: ' + json.dumps(e) + '\n\n' for e in events))
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda system, messages, **kw:
        ToolChat(provider, system, messages, transport=httpx.MockTransport(handler)))
    events = [e async for e in tool_agent.ask_stream('现在部署', [{'role': 'user', 'content': '纠正：现在使用 Linux'}], 'kb',
                                                    deep_ai_options={'max_rounds': 1})]
    assert events[-1]['type'] == 'done'
    final = captured[-1]
    text = json.dumps(final, ensure_ascii=False)
    assert final['tools']
    assert final['tool_choice'] == ('none' if style in ('chat', 'responses') else {'type': 'none'})
    assert 'call1' in text
    if style == 'responses':
        assert 'opaque' in text
    assert '纠正：现在使用 Linux' in text and '现在部署' in text


@pytest.mark.asyncio
async def test_agent_preserves_prior_analysis_and_review_state(knowledge, monkeypatch):
    point = dict(id='install', question='失败如何检查？', status='partial', finding='检查依赖版本和安装日志',
                 evidence=[dict(citation=1, quote='失败时检查依赖版本和安装日志。')], gap='未提供具体报错')
    model = Model([
        [ToolCall('read', 'read_document', dict(document_id=1, version='v1', section=1))],
        [ToolCall('review', 'record_evidence_review', {'points': [point]})], []])
    model.messages = []
    original = model.turn
    async def turn(*args, **kw):
        model.messages.append({'role': 'assistant', 'content': '未经证实的早期草稿'})
        return await original(*args, **kw)
    model.turn = turn
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *a, **k: model)
    events = [e async for e in tool_agent.ask_stream('如何排障？', [], 'kb')]
    assert events[-1]['type'] == 'done'
    data = json.dumps(model.messages, ensure_ascii=False)
    assert '未经证实的早期草稿' in data
    assert next(e['data'] for e in events if e['type'] == 'stage' and e['data']['stage'] == 'answer_context')['context_mode'] == 'native'
    assert model.returned[1][1]['recorded']
    summary = next(e['data'] for e in events if e['type'] == 'stage' and e['data']['stage'] == 'evidence_summary')
    assert summary['stop_reason'] == 'model_ready'
    assert summary['unresolved_points'] == 1


@pytest.mark.asyncio
async def test_truncated_tool_arguments_never_execute(knowledge):
    provider = OpenAIProvider('test', {'base_url': 'https://example.test/v1', 'chat_model': 'test'}, 'secret')
    body = wire_response('chat')
    body['choices'][0]['finish_reason'] = 'length'
    model = ToolChat(provider, 's', [], transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    try:
        with pytest.raises(ToolProtocolError):
            await model.turn(kt.TOOLS)
        assert not model.messages
    finally:
        await model.close()


@pytest.mark.asyncio
async def test_empty_search_still_allows_labelled_general_answer(knowledge, monkeypatch):
    monkeypatch.setattr(kt.retrieval, 'search', lambda *args, **kwargs: ([], 'embed'))
    model = Model([[ToolCall('1', 'search_knowledge', {'query': 'unrelated'})], []])
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *args, **kwargs: model)
    events = [e async for e in rag.ask_stream('q', kb_scope='kb', mode='deep_ai')]
    assert events[-1]['type'] == 'done'
    assert events[-1]['data']['sources'] == []
    assert '通用知识' in events[-1]['data']['answer'] and '[2]' not in events[-1]['data']['answer']


@pytest.mark.asyncio
async def test_document_changed_during_generation_prevents_done(knowledge, monkeypatch):
    model = Model([[ToolCall('1', 'search_knowledge', {'query': 'ADT'})], []])
    async def final(*args, **kwargs):
        yield 'partial'
        knowledge[0][0]['content_hash'] = 'new-version'
        yield 'answer'
    model.final_stream = final
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *args, **kwargs: model)
    events = [e async for e in rag.ask_stream('q', kb_scope='kb', mode='deep_ai')]
    assert events[-1]['type'] == 'error' and '更新' in events[-1]['data']['message']
    assert model.closed


@pytest.mark.asyncio
async def test_answer_failure_retains_read_evidence(knowledge, monkeypatch):
    model = Model([[ToolCall('1', 'search_knowledge', {'query': 'ADT'})], []])
    async def final(*args, **kwargs):
        raise ToolProtocolError('interrupted')
        yield
    model.final_stream = final
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *args, **kwargs: model)
    events = [e async for e in rag.ask_stream('q', kb_scope='kb', mode='deep_ai')]
    assert events[-1]['type'] == 'done'
    assert events[-1]['data']['verification'] == 'partial'
    assert '已找到的原文' in events[-1]['data']['answer'] and events[-1]['data']['sources']


@pytest.mark.asyncio
async def test_timed_out_worker_cannot_add_late_citations(knowledge, monkeypatch):
    import time
    original_read, original_wait = kt.KnowledgeTools.read, asyncio.wait_for
    tool_waits = 0
    def slow_read(self, **kwargs):
        time.sleep(.08)
        return original_read(self, **kwargs)
    async def quick_timeout(awaitable, timeout):
        nonlocal tool_waits
        if timeout is not None and 24 <= timeout <= 25:
            tool_waits += 1
        return await original_wait(awaitable, .01 if timeout is not None and 24 <= timeout <= 25 and tool_waits == 2 else timeout)
    monkeypatch.setattr(kt.KnowledgeTools, 'read', slow_read)
    monkeypatch.setattr(asyncio, 'wait_for', quick_timeout)
    model = Model([[ToolCall('1', 'search_knowledge', {'query': 'ADT'})],
                   [ToolCall('2', 'read_document', {'document_id': 1, 'version': 'v1', 'section': 1})], []])
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *args, **kwargs: model)
    events = [e async for e in rag.ask_stream('q', kb_scope='kb', mode='deep_ai')]
    assert events[-1]['type'] == 'done'
    assert len(events[-1]['data']['sources']) == 1
    assert model.returned[-1][1]['error'] == 'timeout'
    await asyncio.sleep(.15)
    assert len(events[-1]['data']['sources']) == 1


@pytest.mark.asyncio
async def test_permission_revoked_during_output_stops_answer(knowledge, monkeypatch):
    model = Model([[ToolCall('1', 'search_knowledge', {'query': 'ADT'})], []])
    def deny(self):
        raise HTTPException(403, '知识库权限已撤销')
    async def final(*args, **kwargs):
        yield 'partial'
        monkeypatch.setattr(kt.KnowledgeTools, 'check', deny)
        yield 'must not publish'
    model.final_stream = final
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *args, **kwargs: model)
    events = [e async for e in rag.ask_stream('q', kb_scope='kb', mode='deep_ai')]
    assert events[-1]['type'] == 'error' and '撤销' in events[-1]['data']['message']
    assert 'must not publish' not in str(events) and model.closed


@pytest.mark.asyncio
async def test_probe_verifies_actual_tool_result_without_knowledge(knowledge, monkeypatch):
    from src.core import tool_chat
    model = Model([[ToolCall('1', 'insight_connection_probe', '{}')]])
    async def final(*args, **kwargs):
        yield model.returned[0][1]['marker']
    model.final_stream = final
    monkeypatch.setattr(tool_chat, 'create_tool_chat', lambda *args, **kwargs: model)
    result = await tool_chat.probe_tools()
    assert result['supported'] is True and model.closed
    assert len(model.returned) == 1 and list(model.returned[0][1]) == ['marker']


@pytest.mark.asyncio
async def test_all_five_tools_through_agent_and_log_failure_is_nonfatal(knowledge, monkeypatch):
    model = Model([[ToolCall('1', 'get_kb_overview', {}),
                    ToolCall('2', 'list_documents', {}),
                    ToolCall('3', 'search_knowledge', {'query': 'ADT'}),
                    ToolCall('4', 'get_document_outline', {'document_id': 1}),
                    ToolCall('5', 'read_document', {'document_id': 1, 'version': 'v1', 'section': 1})], []])
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *args, **kwargs: model)
    def log_failure(**kwargs):
        raise RuntimeError('log unavailable')
    monkeypatch.setattr(tool_agent.ai_call_logger, 'log_call', log_failure)
    events = [e async for e in tool_agent.ask_stream('q', [], 'kb')]
    assert events[-1]['type'] == 'done' and model.closed
    assert events[-1]['data']['outcome'] == 'success'
    counts = [e['data']['count'] for e in events if e['type'] == 'stage' and e['data']['stage'] == 'knowledge_tool']
    assert counts == [1, 1, 1, 2, 1]
    assert len(model.returned) == 5 and model.returned[0][1]['documents'] == 1


@pytest.mark.asyncio
async def test_generation_timeout_preserves_partial_and_persistable_status(knowledge, monkeypatch):
    from src.api.routes_sessions import _trim_trace
    model = Model([[ToolCall('1', 'search_knowledge', {'query': 'ADT'})], []])
    async def final(*args, **kwargs):
        yield '已生成的回答[1]'
        raise TimeoutError('generation budget exhausted')
    model.final_stream = final
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *args, **kwargs: model)
    events = [e async for e in tool_agent.ask_stream('q', [], 'kb')]
    done = events[-1]['data']
    assert events[-1]['type'] == 'done' and done['outcome'] == 'partial'
    assert done['verification'] == 'partial' and '超时' in done['reason']
    assert done['answer'].count('已生成的回答') == 1
    assert _trim_trace(done['trace'])[-1]['status'] == 'partial'


@pytest.mark.asyncio
async def test_failed_turn_includes_persistable_trace(knowledge, monkeypatch):
    model = Model([[]])
    async def fail(*a, **k):
        raise ToolProtocolError('invalid protocol')
    model.turn = fail
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *args, **kwargs: model)
    events = [e async for e in tool_agent.ask_stream('q', [], 'kb')]
    assert events[-1]['data']['outcome'] == 'failed'
    assert events[-1]['data']['trace'][-1]['status'] == 'error'


@pytest.fixture
def no_retry_delay(monkeypatch):
    original_sleep = asyncio.sleep
    async def skip(seconds):
        await original_sleep(0)
    monkeypatch.setattr(asyncio, 'sleep', skip)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['connect', 408, 429, 502, 503, 504, 520, 522, 524, 599])
async def test_api_default_ten_retries_and_no_duplicate_history(knowledge, no_retry_delay, failure):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) <= 10:
            if failure == 'connect':
                raise httpx.ConnectError('TLS connection interrupted')
            return httpx.Response(failure, json={'error': {'message': 'temporarily unavailable'}})
        return httpx.Response(200, json=wire_response('responses'))
    p = OpenAIProvider('test', {'base_url': 'https://example.test/v1/responses', 'chat_model': 'test'}, 'secret')
    model = ToolChat(p, 'system', [], transport=httpx.MockTransport(handler))
    retries = []
    model.on_retry = lambda **data: retries.append(data)
    try:
        turn = await model.turn(kt.TOOLS)
        assert turn.calls and len(requests) == 11 and len(retries) == 10
        assert all(request == requests[0] for request in requests)
        assert len(model.messages) == 2
    finally:
        await model.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('status,retries,expected', [(400, 5, 1), (401, 5, 1), (403, 5, 1), (502, 0, 1), (503, 2, 3)])
async def test_retry_limits_and_permanent_errors(knowledge, no_retry_delay, status, retries, expected):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={'error': {'message': 'failure'}})
    p = OpenAIProvider('test', {'base_url': 'https://example.test', 'chat_model': 'test'}, 'secret')
    model = ToolChat(p, 'system', [], transport=httpx.MockTransport(handler))
    model.retry_count = retries
    try:
        with pytest.raises(ToolProtocolError):
            await model.turn(kt.TOOLS)
        assert len(requests) == expected
    finally:
        await model.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('has_output', [False, True])
async def test_stream_retries_only_before_output(knowledge, no_retry_delay, has_output):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            if not has_output:
                raise httpx.ConnectError('connection failed')
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"answer"},"finish_reason":"stop"}]}\n\n')
    p = OpenAIProvider('test', {'base_url': 'https://example.test', 'chat_model': 'test'}, 'secret')
    model = ToolChat(p, 'system', [], transport=httpx.MockTransport(handler))
    output = []
    try:
        if has_output:
            with pytest.raises(httpx.RemoteProtocolError):
                async for value in model.final_stream(kt.TOOLS, 'answer now'):
                    output.append(value)
            assert output == ['partial'] and len(requests) == 1
        else:
            output = [value async for value in model.final_stream(kt.TOOLS, 'answer now')]
            assert output == ['answer'] and len(requests) == 2
            assert requests[0] == requests[1]
        assert len(model.messages) == 1
    finally:
        await model.close()


@pytest.mark.asyncio
async def test_retry_respects_total_deadline_and_permission_revocation(knowledge, no_retry_delay):
    import time
    requests = []
    def handler(request):
        requests.append(request)
        raise httpx.ConnectError('failed')
    p = OpenAIProvider('test', {'base_url': 'https://example.test', 'chat_model': 'test'}, 'secret')
    def access():
        if requests:
            raise HTTPException(403, 'revoked')
    model = ToolChat(p, 'system', [], check_access=access, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(HTTPException):
            await model.turn(kt.TOOLS)
        assert len(requests) == 1
        requests.clear()
        model.deadline = time.monotonic() + .1
        with pytest.raises(httpx.ConnectError):
            await model.turn(kt.TOOLS)
        assert len(requests) == 1
    finally:
        await model.close()


@pytest.mark.asyncio
async def test_request_timeout_log_is_not_user_cancel(knowledge, monkeypatch):
    import time
    logs = []
    monkeypatch.setattr(tool_agent.ai_call_logger, 'log_call', lambda **kwargs: logs.append(kwargs))
    async def handler(request):
        await asyncio.Event().wait()
    p = OpenAIProvider('test', {'base_url': 'https://example.test', 'chat_model': 'test'}, 'secret')
    model = ToolChat(p, 'system', [], transport=httpx.MockTransport(handler))
    model.deadline = time.monotonic() + .03
    try:
        with pytest.raises(TimeoutError):
            await model.turn(kt.TOOLS)
        diagnostic = json.loads(logs[-1]['error_message'])
        assert diagnostic['failure_kind'] == 'timeout'
        assert diagnostic['exception_chain'][0]['type'] == 'TimeoutError'
    finally:
        await model.close()


@pytest.mark.asyncio
async def test_sse_permission_checks_do_not_block_event_loop(knowledge, monkeypatch):
    import time
    from src.api import routes_qa
    ticks = []
    monkeypatch.setattr(accounts, 'enabled', True)
    monkeypatch.setattr(accounts, 'require_kb', lambda *args: time.sleep(.06))
    monkeypatch.setattr(accounts, 'selected_provider', SimpleNamespace(get=lambda: None))
    async def source(**kwargs):
        yield {'type': 'token', 'data': {'text': 'a'}}
        yield {'type': 'token', 'data': {'text': 'b'}}
        yield {'type': 'done', 'data': {'answer': 'ab', 'outcome': 'partial'}}
    monkeypatch.setattr(routes_qa, 'ask_stream', source)
    async def heartbeat():
        for _ in range(8):
            await asyncio.sleep(.01)
            ticks.append(time.monotonic())
    async def consume():
        return [e async for e in routes_qa._sse_events(routes_qa.AskRequest(question='q', mode='deep_ai'))]
    events, _ = await asyncio.gather(consume(), heartbeat())
    assert any(b'"text": "ab"' in event for event in events)
    assert max(b - a for a, b in zip(ticks, ticks[1:])) < .05


def test_retry_request_setting_validation():
    from pydantic import ValidationError
    from src.api.routes_qa import AskRequest
    assert AskRequest(question='q').api_retry_count == 10
    assert AskRequest(question='q', api_retry_count=0).api_retry_count == 0
    for invalid in (-1, 11, 1.5, True):
        with pytest.raises(ValidationError):
            AskRequest(question='q', api_retry_count=invalid)


def test_synchronous_endpoint_forwards_retry_setting(knowledge, monkeypatch):
    from src.api import routes_qa
    model = Model()
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *args, **kwargs: model)
    result = routes_qa.ask_endpoint(routes_qa.AskRequest(question='q', kb_scope='kb', mode='deep_ai', api_retry_count=2,
        deep_ai_options={'max_rounds': 1, 'time_limit_enabled': True, 'total_timeout_seconds': 360}))
    assert result.answer and model.retry_count == 2 and model.closed
    assert len(model.returned) == 1


@pytest.mark.asyncio
async def test_cancelling_retry_wait_never_starts_next_request(knowledge):
    requests = []
    retrying = asyncio.Event()
    def handler(request):
        requests.append(request)
        raise httpx.ConnectError('connection failed')
    p = OpenAIProvider('test', {'base_url': 'https://example.test', 'chat_model': 'test'}, 'secret')
    model = ToolChat(p, 'system', [], transport=httpx.MockTransport(handler))
    model.on_retry = lambda **kwargs: retrying.set()
    task = asyncio.create_task(model.turn(kt.TOOLS))
    try:
        await asyncio.wait_for(retrying.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(requests) == 1
    finally:
        await model.close()


def test_citation_filter_preserves_indices_across_every_stream_boundary():
    text = "数组下标与档位对应：\n\n- `[0]`：买一／卖一\n- `[1]`：买二／卖二\n- `[9]`：买十／卖十\n\narray[1] [0] [99] [链接](https://example.com/1)\n```python\na[0] = a[9]\n```\n来源【cite:1】无效【cite:99】"
    expected = text.replace("【cite:99】", "")
    for cut in range(len(text) + 1):
        f = tool_agent.CitationFilter(1)
        assert f.feed(text[:cut]) + f.feed(text[cut:]) + f.feed("", final=True) == expected
    f = tool_agent.CitationFilter(1)
    assert "".join(f.feed(char) for char in text) + f.feed("", final=True) == expected


@pytest.mark.asyncio
async def test_deep_history_tools_and_complete_assistant_context(knowledge, monkeypatch):
    from src.qa import conversation_context as cc
    context = cc.Context('session', 'now', 'kb', turns=[{'id': 'old', 'order': 0, 'question': '怎么部署', 'answer': '第二种是容器部署', 'status': 'complete'}])
    cc.assemble(context, '第二种怎么做', {'content': '默认中文', 'enabled': True}, None, cc.config())
    model = Model([[ToolCall('h', 'search_conversation_history', {'query': '第二种'})], [ToolCall('k', 'search_knowledge', {'query': '部署'})], []])
    captured = []
    def create(system, messages, **kwargs):
        captured.append((system, messages))
        return model
    monkeypatch.setattr(tool_agent, 'create_tool_chat', create)
    token = cc.active.set(context)
    try:
        events = [event async for event in rag.ask_stream('第二种怎么做', None, 'kb', mode='deep_ai')]
    finally:
        cc.active.reset(token)
    assert events[-1]['type'] == 'done'
    assert '默认中文' in captured[0][0]
    assert '第二种是容器部署' in str(captured[0][1])
    assert model.returned[0][1]['turns'][0]['turn_id'] == 'old'
    assert context.history_reads == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('strategy', ['evidence', 'balanced', 'exploratory'])
async def test_constraint_strategy_reaches_both_stages_and_overrides_legacy(knowledge, monkeypatch, strategy):
    from src.qa import ai_policy
    from src.api.routes_qa import AskRequest
    req = AskRequest(question='安装失败', strict_knowledge=True, deep_ai_options={'constraint_strategy': strategy})
    model = Model()
    systems = []
    def create(system, messages, **kwargs):
        systems.append(system)
        return model
    monkeypatch.setattr(tool_agent, 'create_tool_chat', create)
    events = [e async for e in rag.ask_stream(req.question, kb_scope='kb', mode='deep_ai', strict_knowledge=req.strict_knowledge, deep_ai_options=req.deep_ai_options)]
    assert events[-1]['type'] == 'done'
    for system in (systems[0], model.system):
        assert system.count(ai_policy.STRATEGIES[strategy]) == 1
        assert all(text not in system for key, text in ai_policy.STRATEGIES.items() if key != strategy)
    assert all('next_step_hint' not in result and 'review_hint' not in result for _, result in model.returned)


def test_constraint_strategy_validation_and_legacy_defaults():
    from src.core.deep_ai_options import DeepAiOptions
    from src.qa.ai_policy import resolve
    from pydantic import ValidationError
    assert resolve(None, True) == 'evidence'
    assert resolve(None, False) == 'balanced'
    assert resolve('exploratory', True) == 'exploratory'
    with pytest.raises(ValidationError):
        DeepAiOptions(constraint_strategy='invalid')


@pytest.mark.asyncio
async def test_lookup_failure_is_partial_even_when_answer_succeeds(knowledge, monkeypatch):
    from src.core.tool_chat import ToolHTTPError
    model = Model()
    original = model.turn
    attempts = 0
    async def turn(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts > 1:
            raise ToolHTTPError('gateway failure', 520, 60)
        return await original(*args, **kwargs)
    model.turn = turn
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *a, **k: model)
    events = [e async for e in tool_agent.ask_stream('安装失败', [], 'kb')]
    done = events[-1]['data']
    assert events[-1]['type'] == 'done' and done['outcome'] == 'partial'
    assert 'HTTP 520' in done['reason']
    assert done['answer'].startswith('> 资料查阅中断')
    assert ''.join(e['data']['text'] for e in events if e['type'] == 'token') == done['answer']
    assert done['trace'][-1]['status'] == 'partial'
    assert next(e for e in done['trace'] if e['stage'] == 'evidence_summary')['status'] == 'partial'
    assert model.closed


@pytest.mark.asyncio
async def test_read_reference_survives_tool_worker_handoff(knowledge, monkeypatch):
    model = Model()
    counter = 0
    async def turn(*args, **kwargs):
        nonlocal counter
        counter += 1
        if counter == 1:
            return ToolTurn('', [ToolCall('s', 'search_document', {'query':'ADT','document_id':1})], {})
        if counter == 2:
            ref = model.returned[-1][1]['evidence'][0]['read_ref']
            return ToolTurn('', [ToolCall('r', 'read_document', {'read_ref':ref})], {})
        return ToolTurn('', [], {})
    model.turn = turn
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *a, **k: model)
    events = [e async for e in tool_agent.ask_stream('ADT', [], 'kb')]
    assert events[-1]['type'] == 'done'
    assert 'error' not in model.returned[-1][1]
    assert model.returned[-1][1]['evidence'][0]['reading']['kind'] == 'section'


def test_search_centers_late_symbol_and_refs_validate_version_and_scope(knowledge):
    files,chunks = knowledge
    chunks['a']['text'] = 'prefix\n' * 600 + 'TargetField 定义在后半段\n' + 'tail' * 1500
    kb = kt.KnowledgeTools('kb')
    found = kb.execute('search_document', dict(query='TargetField', document_id=1, match_mode='exact'))
    ev = found['evidence'][0]
    assert 'TargetField' in ev['text'] and ev['offset'] > 2500
    page = kb.execute('read_document', {'read_ref':ev['read_ref']})
    assert 'TargetField' in page['evidence'][0]['text']
    assert page['next_read_ref']
    other = kt.KnowledgeTools('other')
    assert other.execute('read_document', {'read_ref':ev['read_ref']})['error'] == 'invalid_read_ref'
    files[0]['content_hash'] = 'v2'
    assert kb.execute('read_document', {'read_ref':ev['read_ref']})['error']


def test_outline_reference_and_table_headers_survive_final_payload(knowledge):
    from src.qa.evidence_state import answer_payload, Review
    from src.qa.conversation_context import estimate
    files,chunks=knowledge
    chunks['a']['text']='| A | B |\n| --- | --- |\n| old | row |\n\nparagraph\n\n| 市场 | 字段 |\n| --- | --- |\n| 上海 | TargetField |\n| 深圳 | OtherField |'
    kb=kt.KnowledgeTools('kb')
    outline=kb.outline(1)
    assert kb.execute('read_document', {'read_ref':outline['sections'][0]['read_ref']})['evidence']
    start=chunks['a']['text'].index('| 上海')
    page=kb.read(1,'v1',offset=start)
    assert page['context_header'].startswith('| 市场 | 字段 |')
    assert page['evidence'][0]['structure']['table']['rows'][0]['cells']==['上海','TargetField']
    packet=answer_payload(kb.hits,Review(),[],20000,estimate)
    assert any(e['structure'] and e['structure'].get('table',{}).get('header')=='| 市场 | 字段 |' for e in packet['evidence'])
    assert page['next_section_ref']


def test_tool_discovery_catalog_and_schema(knowledge):
    kb=kt.KnowledgeTools('kb',question='ADT')
    overview=kb.overview()
    assert {v['document_id'] for v in overview['document_catalog']}=={1}
    assert not overview['catalog_truncated']
    full=next(t for t in kt.TOOLS if t['name']=='search_knowledge')
    assert 'document_id' not in full['parameters']['properties']
    assert kb.execute('search_document',dict(query='x',document_id=1,match_mode='bad'))['error']
    assert kb.execute('search_document',dict(query='definitelyAbsent',document_id=1,match_mode='exact'))['status']=='no_match'



def test_structure_never_reuses_other_table_or_includes_unread_row_tail():
    from src.qa.reading_structure import describe
    text='| a | b |\n| --- | --- |\n| old | table |\n\nparagraph\n\n| x | y |\n| --- | --- |\n| new | table |'
    assert 'table' not in describe(text,text.index('paragraph'),text.index('paragraph')+4)
    current=describe(text,text.index('| new'),len(text))
    assert current['table']['header']=='| x | y |'
    partial=describe(text,text.index('| new'),text.index('| new')+6)
    assert partial['table']['rows']==[]
    both=describe(text,0,len(text))
    assert len(both['tables'])==2 and 'table' not in both


def test_catalog_bounded_and_relevant(knowledge):
    files,_=knowledge
    for i in range(2,42):files.append({**files[0],'id':i,'relative_path':f'generic-{i}.md'})
    files.append({**files[0],'id':50,'relative_path':'TargetField-definition.md'})
    result=kt.KnowledgeTools('kb',question='TargetField').overview()
    assert result['catalog_truncated']
    assert result['document_catalog'][0]['document_id']==50
    assert len(result['document_catalog'])<=12
    assert len(json.dumps(result['document_catalog'],ensure_ascii=False))<4600


def test_read_pages_end_on_lines_without_losing_bytes(knowledge):
    _, chunks = knowledge
    chunks['a']['text'] = ('| 字段 | 一段完整的说明 |\n' * 500)
    kb = kt.KnowledgeTools('kb')
    pages = []
    offset = 0
    while True:
        result = kb.read(1, 'v1', offset=offset)
        pages.append(result['evidence'][0]['text'])
        offset = result['next_offset']
        if offset is None:
            break
        assert pages[-1].endswith('\n')
    assert ''.join(pages) == chunks['a']['text']


@pytest.mark.asyncio
async def test_capacity_fallback_keeps_recent_analysis_and_history(knowledge, monkeypatch):
    model = Model([[ToolCall('read', 'read_document', dict(document_id=1, version='v1', section=1))], []])
    model.messages = [{'role':'assistant', 'content':'x' * 600000 + '已有分析：先核对依赖版本'}]
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *a, **k: model)
    events = [e async for e in tool_agent.ask_stream('下一步呢？', [{'role':'user', 'content':'只考虑 Linux 离线环境'}], 'kb')]
    assert events[-1]['type'] == 'done'
    stage = next(e['data'] for e in events if e['type']=='stage' and e['data']['stage']=='answer_context')
    assert stage['context_mode'] == 'compact_fallback'
    content = json.dumps(model.messages, ensure_ascii=False)
    assert '已有分析：先核对依赖版本' in content
    assert '只考虑 Linux 离线环境' in content
    assert '失败时检查依赖版本和安装日志。' in content
