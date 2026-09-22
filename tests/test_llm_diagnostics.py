import json
import logging

import httpx
import pytest

from src.core import accounts, ai_call_logger, llm_diagnostics as diagnostics
from src.core.llm_providers import OpenAIProvider
from src.core.tool_chat import ToolChat, ToolProtocolError


@pytest.fixture
def logs(monkeypatch):
    saved = []
    monkeypatch.setattr(ai_call_logger, 'log_call', lambda **kwargs: saved.append(kwargs))
    token = accounts.identity.set({'id': 'user1', 'username': 'xuanan'})
    pid = accounts.selected_provider.set('provider1')
    monkeypatch.setattr(diagnostics, 'getproxies', lambda: {'https': 'http://proxy-user:proxy-password@127.0.0.1:7890', 'no': 'localhost'})
    yield saved
    accounts.identity.reset(token)
    accounts.selected_provider.reset(pid)


def model(handler, check=lambda: None):
    p = OpenAIProvider('test-provider', {'base_url': 'https://example.test/v1', 'chat_model': 'test-model'}, 'test-secret-key')
    chat = ToolChat(p, 'private system prompt', [{'role': 'user', 'content': 'private knowledge content'}],
                    check_access=check, meta={'session_id': 'session1', 'turn_id': 'turn1', 'kb_id': 'kb1'}, transport=httpx.MockTransport(handler))
    # Diagnostics tests inspect one failure; retry behavior has separate coverage.
    chat.retry_count = 0
    return chat


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
async def test_http_400_retains_redacted_detail_and_correlation(logs, caplog, stream):
    caplog.set_level(logging.INFO, logger=diagnostics.__name__)
    def handler(request):
        assert 'trace' in request.extensions
        return httpx.Response(400, headers={'x-request-id': 'upstream-123', 'server': 'test-gateway', 'set-cookie': 'must-not-log'},
                              json={'error': {'code': 'invalid_parameter', 'param': 'tool_choice',
                                             'message': 'tool_choice required is invalid; api_key=test-secret-key'},
                                    'request': {'messages': 'must-not-log'}})
    chat = model(handler)
    try:
        with pytest.raises(ToolProtocolError) as error:
            if stream:
                _ = [s async for s in chat.final_stream([], 'answer')]
            else:
                await chat.turn([], force=True)
        entry = json.loads(logs[-1]['error_message'])
        assert entry['http_status'] == 400 and entry['phase'] == '收到 HTTP 响应'
        assert entry['endpoint'] == 'https://example.test/v1/chat/completions'
        assert entry['stream'] is stream and entry['user'] == 'xuanan'
        assert entry['session_id'] == 'session1' and entry['turn_id'] == 'turn1'
        assert entry['response_headers']['x-request-id'] == 'upstream-123'
        assert 'invalid_parameter' in entry['response_error']
        assert entry['call_id'] in str(error.value)
        assert entry['environment_proxies']['https'] == 'http://127.0.0.1:7890'
        visible = caplog.text + logs[-1]['error_message'] + str(error.value)
        for secret in ('test-secret-key', 'proxy-password', 'proxy-user', 'must-not-log', 'private system prompt', 'private knowledge content'):
            assert secret not in visible
        assert 'request_start' in caplog.text and 'request_failed' in caplog.text
    finally:
        await chat.close()


@pytest.mark.asyncio
async def test_connection_failure_is_distinct_from_http_error(logs):
    async def handler(request):
        await request.extensions['trace']('connection.connect_tcp.started', {'host': '127.0.0.1', 'port': 7890})
        try:
            raise OSError('connection refused')
        except OSError as exc:
            raise httpx.ConnectError('unable to connect', request=request) from exc
    chat = model(handler)
    try:
        with pytest.raises(httpx.ConnectError):
            await chat.turn([])
        detail = json.loads(logs[-1]['error_message'])
        assert detail['http_status'] is None
        assert detail['connect_host'] == '127.0.0.1' and detail['connect_port'] == 7890
        assert [e['type'] for e in detail['exception_chain']] == ['ConnectError', 'OSError']
        assert detail['phase'] == '发起 HTTP 请求'
    finally:
        await chat.close()


@pytest.mark.asyncio
async def test_local_preflight_error_never_claims_request_was_sent(logs, caplog):
    def deny():
        raise ValueError('permission revoked')
    def never(request):
        pytest.fail('HTTP request must not be sent')
    chat = model(never, deny)
    try:
        with pytest.raises(ValueError):
            await chat.turn([])
        detail = json.loads(logs[-1]['error_message'])
        assert detail['phase'] == '权限检查' and detail['http_status'] is None
        assert 'endpoint' not in detail and 'request_headers_sent' not in caplog.text
    finally:
        await chat.close()


@pytest.mark.asyncio
async def test_error_body_is_bounded_and_redacts_credentials(logs):
    chat = model(lambda request: httpx.Response(502, text='https://user:pass@example.test/path?token=secret\nAuthorization: Bearer hidden\n' + 'x' * 20000))
    try:
        with pytest.raises(ToolProtocolError):
            await chat.turn([])
        detail = json.loads(logs[-1]['error_message'])
        assert detail['response_body_truncated']
        assert len(detail['response_error']) < 4100
        for secret in ('user:pass', 'token=secret', 'Bearer hidden'):
            assert secret not in logs[-1]['error_message']
    finally:
        await chat.close()


@pytest.mark.asyncio
async def test_invalid_json_has_parse_phase_and_exception_chain(logs):
    chat = model(lambda request: httpx.Response(200, text='not-json'))
    try:
        with pytest.raises(ToolProtocolError):
            await chat.turn([])
        detail = json.loads(logs[-1]['error_message'])
        assert detail['phase'] == '解析工具响应' and detail['http_status'] == 200
        assert any(e['type'] == 'JSONDecodeError' for e in detail['exception_chain'])
    finally:
        await chat.close()
