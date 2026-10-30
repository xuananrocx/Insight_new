# ruff: noqa: F811 -- imported pytest fixtures are injected by name
import json
import httpx
import pytest
from src.core.config import settings
from src.core.llm_providers import OpenAIProvider, AnthropicProvider
from src.core.tool_chat import ToolChat
from src.core.tool_stream import Assembly
from src.core.web_search import WebEvents
from src.api.routes_sessions import _trim_trace
from tests.test_accounts_access import env, actor  # noqa: F401
from tests.test_api_models import create, update
from tests.test_provider_edits import body, save, model
from src.core import accounts


def test_model_configuration_survives_rename_and_legacy_update(env):
    pid = create(env.alice, model_ids=('a',))
    assert update(env.alice, pid, models=[{'model_id':'a','web_search':'responses'}]).status_code == 200
    assert update(env.alice, pid, models=[{'model_id':'a','display_name':'renamed'}]).status_code == 200
    data = body(env.alice, pid, [model('a','b')])
    assert save(env.alice,pid,data).status_code == 200
    with actor(env.alice_id):
        assert accounts.resolve_provider(pid,'b')['web_search'] == 'responses'
    assert update(env.alice,pid,models=[{'model_id':'b','web_search':'anthropic'}]).status_code == 422
    assert update(env.alice,pid,models=[{'model_id':'b','web_search':'off'}]).status_code == 200


@pytest.mark.asyncio
async def test_global_gate_and_search_payload(monkeypatch):
    p=OpenAIProvider('test',{'base_url':'https://example.test/v1','chat_model':'test'},'key')
    p.web_search_mode='responses'
    monkeypatch.setattr(settings,'_config',{})
    chat=ToolChat(p,'system',[])
    assert chat.style == 'chat'
    assert 'tools' not in chat._request([])[2]
    await chat.close()
    monkeypatch.setattr(settings,'_config',{'qa':{'ai_generation':{'web_search_enabled':True}}})
    chat=ToolChat(p,'system',[])
    url,_,payload=chat._request([{'name':'kb','description':'kb','parameters':{'type':'object','properties':{}}}])
    assert url.endswith('/v1/responses')
    assert [t['type'] for t in payload['tools']] == ['function','web_search']
    assert chat._request([], final=True)[2]['tool_choice'] == 'none'
    await chat.close()


def test_search_errors_sources_and_persistence():
    events=[];web=WebEvents(events.append)
    web.feed({'type':'content_block_start','content_block':{'type':'server_tool_use','id':'s1','name':'web_search'}})
    web.feed({'type':'content_block_start','content_block':{'type':'web_search_tool_result','tool_use_id':'s1','content':[{'type':'web_search_result','title':'Python','url':'https://python.org','encrypted_content':'SECRET'},{'type':'web_search_result','url':'javascript:alert(1)'}]}})
    web.feed({'type':'content_block_delta','delta':{'type':'citations_delta','citation':{'type':'web_search_result_location','url':'https://python.org','title':'Python'}}})
    web.feed({'type':'message_stop'})
    saved=_trim_trace(events)
    assert saved[-1]['web_sources']==[{'url':'https://python.org','title':'Python','cited':True}]
    assert 'SECRET' not in json.dumps(saved)
    web.feed({'type':'content_block_start','content_block':{'type':'web_search_tool_result','tool_use_id':'s2','content':{'type':'web_search_tool_result_error','error_code':'unavailable'}}})
    assert events[-1]['status']=='failed'
    assert web.sources['https://python.org']['cited']


@pytest.mark.asyncio
async def test_anthropic_pause_retains_server_blocks_without_local_calls(monkeypatch):
    monkeypatch.setattr(settings,'_config',{'qa':{'ai_generation':{'web_search_enabled':True}}})
    p=AnthropicProvider('test',{'base_url':'https://example.test','chat_model':'test'},'key');p.web_search_mode='anthropic'
    chat=ToolChat(p,'system',[])
    assembly=Assembly('anthropic')
    for event in [
        {'type':'content_block_start','index':0,'content_block':{'type':'server_tool_use','id':'s1','name':'web_search','input':{}}},
        {'type':'content_block_delta','index':0,'delta':{'type':'input_json_delta','partial_json':'{"query":"python"}'}},
        {'type':'content_block_start','index':1,'content_block':{'type':'web_search_tool_result','tool_use_id':'s1','content':[{'type':'web_search_result','url':'https://python.org','encrypted_content':'opaque'}]}},
        {'type':'message_delta','delta':{'stop_reason':'pause_turn'}}]:assembly.feed(event)
    turn=chat._decode(assembly.wire())
    assert turn.paused and not turn.calls
    assert chat.messages[-1]['content'][1]['content'][0]['encrypted_content']=='opaque'
    assert chat._request([])[2]['tools'][0]['type']=='web_search_20250305'
    assert chat._request([], final=True)[2]['tool_choice'] == {'type': 'none'}
    await chat.close()


@pytest.mark.asyncio
async def test_responses_stream_keeps_citations_and_search_context(monkeypatch):
    monkeypatch.setattr(settings,'_config',{'qa':{'ai_generation':{'web_search_enabled':True}}})
    p=OpenAIProvider('test',{'base_url':'https://example.test/v1','chat_model':'test'},'key');p.web_search_mode='responses'
    output=[{'type':'web_search_call','id':'w1','status':'completed','action':{'type':'search'}},{'type':'message','role':'assistant','content':[{'type':'output_text','text':'answer','annotations':[{'type':'url_citation','title':'Python','url':'https://python.org'}]}]}]
    def handler(request):
        assert json.loads(request.content)['tools'][0]['type']=='web_search'
        events=[{'type':'response.output_text.delta','delta':'answer'},{'type':'response.completed','response':{'status':'completed','output':output}}]
        return httpx.Response(200,text=''.join('data: '+json.dumps(e)+'\n\n' for e in events))
    chat=ToolChat(p,'system',[],transport=httpx.MockTransport(handler));events=[];chat.on_web_event=events.append
    try:
        result=[x async for x in chat.stream_turn([])]
        assert result[0]==('text','answer')
        assert not result[-1][1].calls
        assert chat.messages==output
        assert events[-1]['web_sources'][0]['cited']
    finally:await chat.close()


def test_responses_search_results_are_not_citations_and_events_deduplicate():
    events = []
    web = WebEvents(events.append)
    event = {'type': 'response.output_item.done', 'item': {
        'type': 'web_search_call', 'id': 's1', 'status': 'completed',
        'action': {'type': 'search', 'sources': [{'url': 'https://python.org', 'title': 'Python'}]}}}
    web.feed(event)
    web.feed(event)
    assert len(events) == 1
    assert events[0]['web_sources'][0]['cited'] is False
    web.item({'type': 'web_search_call', 'id': 's2', 'status': 'failed'})
    assert events[-1]['status'] == 'failed'


def test_web_probe_requires_model_access_and_configuration(env, monkeypatch):
    from src.core import web_search
    calls = []
    async def probe(mode):
        calls.append(mode)
        return {'supported': True, 'message': 'ok', 'sources': []}
    monkeypatch.setattr(web_search, 'probe', probe)
    pid = create(env.alice, model_ids=('a',))
    url = f'/api/v1/providers/{pid}/test-web-search?model_id=a'
    assert env.alice.post(url).status_code == 400
    assert update(env.alice, pid, models=[{'model_id': 'a', 'web_search': 'responses'}]).status_code == 200
    assert env.bob.post(url).status_code in (403, 404)
    assert calls == []
    assert env.alice.post(url).json()['supported']
    assert calls == ['responses']
