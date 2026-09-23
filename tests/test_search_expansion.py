"""Mode consolidation: keep originals and save only completed expansion runs."""
import asyncio
import json
import sqlite3
import uuid

import pytest
from fastapi import HTTPException
from src.api import routes_qa as qa, routes_sessions as sessions
from src.core import accounts
from src.db import metadata_db as db


@pytest.fixture
def req(monkeypatch):
    monkeypatch.setattr(accounts, "enabled", False)
    db.init_db()
    sid = "expand-" + uuid.uuid4().hex
    db.create_session(sid, "test", 1, 1, kb_scope="default", retrieval_mode="deep")
    db.add_turn(sid, sid + "-t", None, "question", "", '[{"content":"original"}]',
                "[]", None, False, None, 1, mode="basic")
    yield qa.AskRequest(question="question", session_id=sid, turn_id=sid + "-t",
                        mode="deep", operation="expand", kb_scope="default")
    db.delete_session(sid)


def saved(req):
    return db.get_session(req.session_id)["turns"][0]


def test_legacy_mode_and_explicit_api_compatibility(req):
    assert qa._resolve_mode(req.model_copy(update={"mode": None, "operation": "ask"})) == "basic"
    assert qa._resolve_mode(req) == "deep"
    assert db.get_session(req.session_id)["retrieval_mode"] == "deep"
    assert qa._resolve_mode(qa.AskRequest(question="q")) == "ai"


def test_expansion_persistence_and_export_import(req):
    result = qa._save_expansion(req, {"sources": [{"content": "expanded"}], "trace": []})
    turn = saved(req)
    assert turn["sources"] == [{"content": "original"}]
    assert turn["mode"] == "basic"
    assert turn["expansion"] == result
    assert sessions.SessionDetail(**sessions.get_session(req.session_id)).turns[0].expansion == result
    imported = sessions._import_one_session(sessions._build_session_payload(req.session_id))
    try:
        restored = db.get_session(imported["id"])["turns"][0]
        assert restored["expansion"] == result
        assert restored["sources"] == turn["sources"]
    finally:
        db.delete_session(imported["id"])


@pytest.mark.parametrize("change", [{"question": "different"}, {"kb_scope": "different"},
                                     {"turn_id": "missing"}, {"mode": "ai"}])
def test_reject_mismatched_original(req, change):
    with pytest.raises(HTTPException):
        qa._expansion_turn(req.model_copy(update=change))
    assert saved(req)["expansion"] is None


def test_revoked_permission_cannot_expand_or_save(req, monkeypatch):
    monkeypatch.setattr(accounts, "enabled", True)
    monkeypatch.setattr(accounts, "require_owner", lambda *args: None)
    def denied(*args):
        raise HTTPException(403, "revoked")
    monkeypatch.setattr(accounts, "require_kb", denied)
    with pytest.raises(HTTPException, match="403"):
        qa._save_expansion(req, {"sources": []})
    assert saved(req)["sources"] == [{"content": "original"}]


def test_success_then_failure_keeps_last_expansion(req, monkeypatch):
    async def success(**kwargs):
        assert kwargs["mode"] == "deep"
        yield {"type": "done", "data": {"sources": [], "trace": [], "answer": ""}}
    monkeypatch.setattr(qa, "ask_stream", success)
    async def collect():
        return [frame async for frame in qa._sse_events(req)]
    frames = asyncio.run(collect())
    assert b'event: done' in frames[-1]
    previous = saved(req)["expansion"]
    assert previous["sources"] == []  # A successful zero-hit search still persists.
    async def fail(**kwargs):
        yield {"type": "error", "data": {"message": "failure"}}
    monkeypatch.setattr(qa, "ask_stream", fail)
    assert b'event: error' in asyncio.run(collect())[-1]
    assert saved(req)["expansion"] == previous
    assert not qa._active_expansions


def test_cancel_and_duplicate_do_not_save(req, monkeypatch):
    async def run(**kwargs):
        yield {"type": "stage", "data": {"stage": "search", "label": "search"}}
        await asyncio.Event().wait()
    monkeypatch.setattr(qa, "ask_stream", run)
    async def check():
        stream = qa._sse_events(req)
        assert b"event: stage" in await anext(stream)
        duplicate = [frame async for frame in qa._sse_events(req)]
        assert b"event: error" in duplicate[0]
        await stream.aclose()
    asyncio.run(check())
    assert not qa._active_expansions
    assert saved(req)["expansion"] is None
    assert saved(req)["sources"] == [{"content": "original"}]


def test_v17_migration_preserves_rows():
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("CREATE TABLE turns(id TEXT, sources_json TEXT)")
    cur.execute("INSERT INTO turns VALUES ('t', 'original')")
    db._migrate_v17_to_v18(cur)
    db._migrate_v17_to_v18(cur)
    assert cur.execute("SELECT * FROM turns").fetchone() == ("t", "original", None)
    conn.close()


@pytest.mark.parametrize("denied", [False, True])
def test_delayed_access_retains_identity_and_denial(req, monkeypatch, denied):
    import contextvars
    import time
    identity = contextvars.ContextVar("test_access_identity", default=None)
    monkeypatch.setattr(qa, "_ACCESS_WAIT", 0.005)
    monkeypatch.setattr(qa, "_ACCESS_GRACE", 1)
    def check():
        assert identity.get() == "owner"
        time.sleep(0.03)
        if denied:
            raise HTTPException(403, "revoked")
    async def run():
        token = identity.set("owner")
        try:
            await qa._check_stream_access(check, req)
        finally:
            identity.reset(token)
    if denied:
        with pytest.raises(HTTPException):
            asyncio.run(run())
    else:
        asyncio.run(run())


def test_pipeline_disconnect_drains_late_cancellation(monkeypatch):
    import threading
    from src.qa import rag
    release = threading.Event()
    finished = threading.Event()
    def pipeline(*args):
        trace = args[3]
        try:
            with trace.stage("search", "search"):
                pass
            release.wait(2)
            trace.cancel_check()
        finally:
            finished.set()
        raise AssertionError("cancellation was not signalled")
    monkeypatch.setattr(rag, "_run_pipeline", pipeline)
    async def run():
        errors = []
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda loop, context: errors.append(context))
        stream = rag.ask_stream("q", mode="basic")
        try:
            # Warmup events may precede the pipeline stage.
            while (await anext(stream))["type"] != "stage":
                pass
            await stream.aclose()
            release.set()
            assert await asyncio.to_thread(finished.wait, 2)
            await asyncio.sleep(0.02)
            assert not errors
        finally:
            release.set()
            await stream.aclose()
            loop.set_exception_handler(previous)
    asyncio.run(run())
