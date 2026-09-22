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
    monkeypatch.setattr(settings, '_config', {'qa': {}})
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
        for part in ['检查依赖版本和日志。[', '2] 通用知识：确认网络。无效引用[99', ']']:
            yield part
    async def close(self):
        self.closed = True


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
    assert '[2]' in result['answer'] and '[99]' not in result['answer']
    assert result['sources'][1]['content'] == knowledge[1]['b']['text']
    assert '严格知识库模式' in prompts[0][0]
    assert 'invented fact' not in str(prompts) and 'Windows 11' in str(prompts)
    assert any(e['type'] == 'token' for e in events)
    assert len(model.returned) == 3


@pytest.mark.asyncio
async def test_no_native_tool_call_is_clear_error(knowledge, monkeypatch):
    model = Model([[]])
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *args, **kwargs: model)
    events = [e async for e in rag.ask_stream('q', kb_scope='kb', mode='deep_ai')]
    assert events[-1]['type'] == 'error' and 'AI 增强' in events[-1]['data']['message'] and model.closed


@pytest.mark.asyncio
async def test_repeated_tools_are_bounded(knowledge, monkeypatch):
    model = Model([[ToolCall(str(i), 'search_knowledge', {'query': 'ADT'})] for i in range(10)])
    monkeypatch.setattr(tool_agent, 'create_tool_chat', lambda *args, **kwargs: model)
    events = [e async for e in rag.ask_stream('q', kb_scope='kb', mode='deep_ai')]
    assert events[-1]['type'] == 'done' and len(model.returned) == 3


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
        if 24 <= timeout <= 25:
            tool_waits += 1
        return await original_wait(awaitable, .01 if 24 <= timeout <= 25 and tool_waits == 2 else timeout)
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
@pytest.mark.parametrize('failure', ['connect', 429, 502, 503, 504])
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
    assert b'"text": "ab"' in events[0]
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
    result = routes_qa.ask_endpoint(routes_qa.AskRequest(question='q', kb_scope='kb', mode='deep_ai', api_retry_count=2))
    assert result.answer and model.retry_count == 2 and model.closed


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
