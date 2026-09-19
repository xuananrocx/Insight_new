"""Four-mode API, persistence and provider failure contracts."""
from types import SimpleNamespace

import pytest

from src.api import routes_qa, routes_sessions
from src.core import ai_call_logger, llm_client, llm_providers
from src.db import metadata_db
from src.qa import rag


@pytest.mark.parametrize("mode", ["basic", "deep", "ai", "deep_ai"])
def test_mode_survives_update_export_import(mode):
    metadata_db.init_db()
    sid = "four-modes-" + mode
    imported = None
    metadata_db.create_session(sid, "模式测试", 1000, 1000, retrieval_mode=mode)
    try:
        assert routes_qa._resolve_mode(routes_qa.AskRequest(question="q", session_id=sid)) == mode
        assert routes_qa._resolve_mode(routes_qa.AskRequest(question="q", session_id=sid, mode="basic")) == "basic"
        routes_sessions.add_turn(sid, routes_sessions.AddTurnRequest(
            id="t1", question="q", answer="a", created_at=1000, mode=mode,
            sources=[{"content": "证据" * 600}]))
        routes_sessions.update_turn(sid, "t1", routes_sessions.UpdateTurnRequest(
            answer="updated", sources=[{"content": "更新证据" * 400}]))
        payload = routes_sessions._build_session_payload(sid)
        assert payload["session"]["retrieval_mode"] == mode
        assert payload["turns"][0]["mode"] == mode
        expected = 200 if mode == "ai" else 1600
        assert len(payload["turns"][0]["sources"][0]["content"]) == expected
        imported = routes_sessions._import_one_session(payload)["id"]
        restored = metadata_db.get_session(imported)
        assert restored["retrieval_mode"] == mode
        assert restored["turns"][0]["mode"] == mode
    finally:
        metadata_db.delete_session(sid)
        if imported:
            metadata_db.delete_session(imported)


@pytest.mark.parametrize("mode", ["basic", "deep", "deep_ai"])
def test_sync_entry_respects_requested_mode(monkeypatch, mode):
    async def stream(question, history, kb, **kwargs):
        assert kwargs["mode"] == mode
        yield {"type": "done", "data": {"answer": "answer" if mode == "deep_ai" else "",
               "sources": [{"content": "source"}], "used_provider": "test", "used_chunks": 1, "trace": []}}
    monkeypatch.setattr(rag, "ask_stream", stream)
    answer = rag.ask("q", mode=mode)
    assert answer.mode == mode
    assert answer.sources[0]["content"] == "source"
    assert answer.citations[0].text_snippet == "source"


def provider_client(monkeypatch, partial=False):
    called = []

    def failure(*a, **k):
        raise llm_providers.LLMError("offline")

    async def failed_stream(*a, **k):
        if partial:
            yield "half-answer"
        raise llm_providers.LLMError("offline")

    async def backup_stream(*a, **k):
        called.append("backup")
        yield "complete-answer"

    client = object.__new__(llm_client.LLMClient)
    client._chat_chain = ["first", "backup"]
    client._providers = {
        "first": SimpleNamespace(usable=True, chat=failure, chat_stream=failed_stream),
        "backup": SimpleNamespace(usable=True, chat=lambda *a, **k: "complete-answer", chat_stream=backup_stream),
    }
    monkeypatch.setattr(ai_call_logger, "log_call", lambda **kwargs: None)
    return client, called


def test_real_provider_error_type_triggers_sync_fallback(monkeypatch):
    client, _ = provider_client(monkeypatch)
    assert client.chat([]) == ("complete-answer", "backup")


@pytest.mark.asyncio
async def test_stream_failure_before_first_token_uses_backup(monkeypatch):
    client, called = provider_client(monkeypatch)
    assert [v async for v in client.chat_stream([])] == [("complete-answer", "backup")]
    assert called == ["backup"]


@pytest.mark.asyncio
async def test_stream_failure_does_not_mix_two_provider_answers(monkeypatch):
    client, called = provider_client(monkeypatch, partial=True)
    seen = []
    with pytest.raises(llm_providers.LLMError):
        async for value in client.chat_stream([]):
            seen.append(value)
    assert seen == [("half-answer", "first")]
    assert called == []


@pytest.mark.asyncio
async def test_deep_mode_sse_serialization(monkeypatch):
    async def stream(**kwargs):
        assert kwargs["mode"] == "deep_ai"
        yield {"type": "done", "data": {"answer": "已核验 [1]", "used_chunks": 1, "mode": "deep_ai"}}
    monkeypatch.setattr(routes_qa, "ask_stream", stream)
    encoded = b"".join([v async for v in routes_qa._sse_events(
        routes_qa.AskRequest(question="q", mode="deep_ai"))]).decode("utf-8")
    assert "event: done" in encoded and "已核验" in encoded
