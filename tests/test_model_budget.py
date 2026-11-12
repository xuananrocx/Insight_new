"""Budget regressions use isolated config, synthetic content and mocked transports."""
import asyncio
import copy
import json

import httpx
import pytest
import yaml
from fastapi import HTTPException

from src.core import accounts, model_budget as mb
from src.core.config import settings
from src.core.llm_providers import OpenAIProvider, AnthropicProvider, AnthropicArchProvider
from src.core.tool_chat import ToolChat, ToolProtocolError
from src.api import routes_settings as routes


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(settings, '_config', {'qa': {'conversation_context': {'context_window_tokens': 65536}}})
    a = mb.active.set(None)
    b = accounts.provider_snapshot.set(None)
    yield
    accounts.provider_snapshot.reset(b)
    mb.active.reset(a)


def test_reproduces_both_176_limits_without_invalid_floor():
    snapshot = mb.capture()
    decision = snapshot.decide(161920, 55540)
    assert decision.actual_output == 6996 and decision.limited_by == 'context'
    accounts.provider_snapshot.set({'context_tokens': 262144, 'output_tokens': 131072})
    decision = mb.capture().decide(161920, 55540)
    assert decision.actual_output == 131072 and decision.limited_by == 'model'
    with pytest.raises(mb.BudgetError, match='空间不足'):
        snapshot.decide(8192, 65536)


@pytest.mark.parametrize('model,window,output,source', [
    ({},65536,50000,'系统默认窗口'),
    ({'context_tokens':4096},4096,1000,'模型配置'),
    ({'context_tokens':131072},131072,50000,'模型配置'),
    ({'output_tokens':2048},65536,2048,'系统默认窗口'),
    ({'context_tokens':1000000},262144,50000,'应用支持上限'),
])
def test_unknown_partial_and_large_model_capabilities(model,window,output,source):
    accounts.provider_snapshot.set(model)
    budget=mb.capture()
    decision=budget.decide(50000,96)
    assert (decision.window,decision.actual_output,decision.window_source)==(window,output,source)


def test_legacy_fields_and_correct_path_precedence():
    config={'feature_flags':{'conversation_context':{'context_window_tokens':131072,'enabled':True,'summary_tokens':3000}},
            'qa':{'conversation_context':{'context_window_tokens':65536}}}
    values=mb.context_values(config)
    assert values['context_window_tokens']==65536 and values['summary_tokens']==3000
    assert 'enabled' not in values


@pytest.mark.asyncio
async def test_concurrent_snapshots_do_not_leak_and_ignore_midflight_edits():
    async def request(cap):
        token=accounts.provider_snapshot.set({'output_tokens':cap})
        frozen=mb.active.set(mb.capture())
        try:
            await asyncio.sleep(0)
            settings._config['qa']['conversation_context']['context_window_tokens']=262144
            assert mb.current().window==65536
            return mb.current().cap(161920)
        finally:
            mb.active.reset(frozen)
            accounts.provider_snapshot.reset(token)
    assert await asyncio.gather(request(4096),request(8192)) == [4096,8192]
    assert mb.active.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('style', ['chat','responses','anthropic','arch'])
async def test_final_wire_cap_preserves_protocol_payload_and_freezes_model(style):
    accounts.provider_snapshot.set({'context_tokens':262144,'output_tokens':131072})
    cls={'chat':OpenAIProvider,'responses':OpenAIProvider,'anthropic':AnthropicProvider,'arch':AnthropicArchProvider}[style]
    base='https://example.test/v1'+('/responses' if style=='responses' else '')
    provider=cls('test',{'base_url':base,'chat_model':'test'},'secret')
    model=ToolChat(provider,'system',[{'role':'user','content':'question'}])
    try:
        accounts.provider_snapshot.set({'output_tokens':256})
        tool={'name':'read','description':'Read evidence','parameters':{'type':'object','properties':{}}}
        _,_,payload=model._request([tool],max_tokens=161920)
        key='max_output_tokens' if style=='responses' else 'max_tokens'
        assert payload[key]==131072
        assert payload['tools'] and model.messages==[{'role':'user','content':'question'}]
        assert model.last_budget.model_output==131072
    finally:
        await model.close()


@pytest.mark.asyncio
async def test_non_token_incomplete_is_not_reported_as_output_exhaustion():
    provider=OpenAIProvider('test',{'base_url':'https://example.test/v1/responses','chat_model':'test'},'secret')
    def handler(request):
        event={'type':'response.incomplete','response':{'incomplete_details':{'reason':'content_filter'}}}
        return httpx.Response(200,text='data: '+json.dumps(event)+'\n\n')
    model=ToolChat(provider,'s',[],transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ToolProtocolError,match='并非已确认'):
            _=[t async for t in model.final_stream([], 'answer')]
        assert not model.final_truncated
    finally:
        await model.close()


@pytest.fixture
def config_files(tmp_path,monkeypatch):
    import src.core.config as cfg
    user=tmp_path/'config.yaml'
    local=tmp_path/'config.local.yaml'
    project=tmp_path/'project'
    project.mkdir()
    user.write_text(yaml.safe_dump({'qa':{'unrelated':123}}))
    monkeypatch.setattr(cfg,'USER_CONFIG_PATH',user)
    monkeypatch.setattr(cfg,'USER_CONFIG_LOCAL_PATH',local)
    monkeypatch.setattr(cfg,'PROJECT_ROOT',project)
    return user,local


def test_save_persists_only_budget_and_partial_update_preserves_values(config_files):
    user,_=config_files
    result=routes.update_context_budget(mb.ContextSettings(context_window_tokens=131072,history_tokens=12000))
    assert result['context_window_tokens']==131072
    routes.update_context_budget(mb.ContextSettings(summary_tokens=3000))
    saved=yaml.safe_load(user.read_text())
    assert saved['qa']['unrelated']==123
    assert saved['qa']['conversation_context']['history_tokens']==12000
    assert saved['qa']['conversation_context']['summary_tokens']==3000
    assert mb.capture().window==131072


def test_write_failure_keeps_memory_and_overrides_are_blocked(config_files,monkeypatch):
    _,local=config_files
    before=copy.deepcopy(settings.config)
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(routes,'_secure_write_yaml',fail)
    with pytest.raises(OSError):
        routes.update_context_budget(mb.ContextSettings(history_tokens=12000))
    assert settings.config==before
    local.write_text(yaml.safe_dump({'qa':{'conversation_context':{'history_tokens':8000}}}))
    with pytest.raises(HTTPException) as info:
        routes.update_context_budget(mb.ContextSettings(history_tokens=12000))
    assert info.value.status_code==409


def test_enhanced_final_budget_can_use_more_than_reserve_and_model_cap():
    from src.qa import enhanced_ai
    settings._config['qa']['ai_generation']={'max_tokens':161920}
    accounts.provider_snapshot.set({'context_tokens':262144,'output_tokens':131072})
    assert enhanced_ai.chat_options()['max_tokens']==16384
    assert enhanced_ai.chat_options([{'role':'user','content':'q'}])['max_tokens']==131072


@pytest.mark.asyncio
async def test_account_client_direct_provider_caps_sync_and_stream(monkeypatch):
    from src.core.account_llm import AccountChatClient
    from src.core.llm_client import LLMClient
    provider = {'id': 'p', 'name': 'test', 'chat_model': 'model', 'protocol': 'openai',
                'context_tokens': 262144, 'output_tokens': 4096}
    monkeypatch.setattr(accounts, 'provider_config', lambda p: {
        'base_url': 'https://example.test/v1', 'chat_model': 'model', 'api_key': 'fake'})
    checks = []
    monkeypatch.setattr(AccountChatClient, 'check_access', lambda self: checks.append(True))
    calls = []
    def chat(self, messages, **kwargs):
        calls.append(kwargs['max_tokens'])
        return 'answer'
    async def stream(self, messages, **kwargs):
        calls.append(kwargs['max_tokens'])
        yield 'answer'
    monkeypatch.setattr(LLMClient, 'chat', chat)
    monkeypatch.setattr(LLMClient, 'chat_stream', stream)
    client = AccountChatClient(provider)
    assert client.chat([{'role': 'user', 'content': 'q'}], max_tokens=161920) == 'answer'
    assert [x async for x in client.chat_stream([{'role': 'user', 'content': 'q'}], max_tokens=161920)] == ['answer']
    assert calls == [4096, 4096]
    assert len(checks) == 4
