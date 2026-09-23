import asyncio
import json
import httpx
import pytest

from src.core import accounts
from src.core.config import settings
from src.core.llm_providers import OpenAIProvider
from src.core.tool_chat import ToolChat
from src.core.tool_stream import Assembly
from src.qa import tool_agent, knowledge_tools as kt


def sse(events):
    return httpx.Response(200,text=''.join('data: '+json.dumps(event)+'\n\n' for event in events))


@pytest.mark.asyncio
async def test_agent_streams_one_answer_without_second_synthesis(monkeypatch):
    monkeypatch.setattr(accounts,'enabled',False)
    monkeypatch.setattr(settings,'_config',{})
    monkeypatch.setattr(kt.KnowledgeTools,'overview',lambda self:{'documents':0})
    monkeypatch.setattr(kt.KnowledgeTools,'sources',lambda self:[])
    monkeypatch.setattr(kt.KnowledgeTools,'validate_versions',lambda self:None)
    monkeypatch.setattr(tool_agent.ai_call_logger,'log_call',lambda **kw:None)
    requests=[]
    def handler(request):
        payload=json.loads(request.content)
        requests.append(payload)
        assert payload['stream'] and payload['tool_choice']=='auto'
        if len(requests)==1:
            return sse([{'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call1','function':{'name':'get_kb_overview','arguments':'{}'}}]},'finish_reason':'tool_calls'}]}])
        assert any(m.get('role')=='tool' for m in payload['messages'])
        return sse([{'choices':[{'delta':{'content':'承接前文，'},'finish_reason':None}]},
                    {'choices':[{'delta':{'content':'直接回答。'},'finish_reason':'stop'}]}])
    provider=OpenAIProvider('test',{'base_url':'https://example.test','chat_model':'test'},'secret')
    monkeypatch.setattr(tool_agent,'create_tool_chat',lambda system,messages,**kw:ToolChat(provider,system,messages,transport=httpx.MockTransport(handler)))
    events=[e async for e in tool_agent.ask_stream('继续解释',[],'kb')]
    assert len(requests)==2
    assert events[-1]['type']=='done'
    assert events[-1]['data']['answer']=='承接前文，直接回答。'
    assert len([e for e in events if e['type']=='token'])==2


@pytest.mark.asyncio
async def test_partial_stream_is_not_retried_after_text(monkeypatch):
    calls=[]
    monkeypatch.setattr(tool_agent.ai_call_logger,'log_call',lambda **kw:None)
    def handler(request):
        calls.append(request)
        return sse([{'choices':[{'delta':{'content':'已有部分'},'finish_reason':None}]}])
    provider=OpenAIProvider('test',{'base_url':'https://example.test','chat_model':'test'},'secret')
    model=ToolChat(provider,'s',[],transport=httpx.MockTransport(handler))
    values=[]
    try:
        with pytest.raises(httpx.RemoteProtocolError):
            async for kind,value in model.stream_turn([]): values.append(value)
    finally:
        await model.close()
    assert values==['已有部分'] and len(calls)==1


def test_streaming_arguments_and_private_reasoning_are_separate():
    assembly=Assembly('chat')
    assert not assembly.feed({'choices':[{'delta':{'reasoning_content':'private','tool_calls':[{'index':0,'id':'a','function':{'name':'search_document','arguments':'{"query":'}}]}}]})
    assembly.feed({'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'"字段"}'}}]},'finish_reason':'tool_calls'}]})
    message=assembly.wire()['choices'][0]['message']
    assert json.loads(message['tool_calls'][0]['function']['arguments'])=={'query':'字段'}
    assert message['reasoning_content']=='private' and not assembly.text


def test_anthropic_signed_thinking_and_partial_json():
    assembly=Assembly('anthropic')
    assembly.feed({'type':'content_block_start','index':0,'content_block':{'type':'thinking','thinking':''}})
    assembly.feed({'type':'content_block_delta','index':0,'delta':{'type':'thinking_delta','thinking':'private'}})
    assembly.feed({'type':'content_block_delta','index':0,'delta':{'type':'signature_delta','signature':'signed'}})
    assembly.feed({'type':'content_block_start','index':1,'content_block':{'type':'tool_use','id':'a','name':'get_kb_overview','input':{}}})
    assembly.feed({'type':'content_block_delta','index':1,'delta':{'type':'input_json_delta','partial_json':'{}'}})
    assembly.feed({'type':'message_delta','delta':{'stop_reason':'tool_use'}})
    assert not assembly.text
    assert assembly.wire()['content'][0]['signature']=='signed'
    assert assembly.wire()['content'][1]['input']=={}
