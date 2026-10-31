"""Exercise job ownership, persistence and cancellation with no real model calls."""

import asyncio
import json
import uuid

import pytest
from fastapi import HTTPException
from src.qa import answer_jobs as jobs
from src.api import routes_qa, routes_sessions
from src.db import metadata_db as db
from src.core import accounts


@pytest.fixture
def job_request(monkeypatch):
    db.init_db()
    jobs.initialize()
    monkeypatch.setattr(accounts, "enabled", False)
    sid = "job-" + uuid.uuid4().hex
    db.create_session(
        session_id=sid, title="test", created_at=1, updated_at=1, kb_scope="default"
    )
    db.add_turn(
        session_id=sid,
        turn_id=sid + "-t1",
        order_idx=None,
        question="hello",
        answer=None,
        sources_json="[]",
        trace_json="[]",
        used_provider=None,
        liked=False,
        error=None,
        created_at=1,
    )
    return routes_qa.AskRequest(
        session_id=sid,
        turn_id=sid + "-t1",
        question="hello",
        kb_scope="default",
        mode="ai",
    )


def event(kind, value):
    return f"event: {kind}\ndata: {json.dumps(value)}\n\n".encode()


@pytest.mark.asyncio
async def test_detach_resume_idempotency_and_server_owned_save(
    job_request, monkeypatch
):
    gate = asyncio.Event()
    emitted = asyncio.Event()
    calls = 0

    async def stream(req):
        nonlocal calls
        calls += 1
        yield event("token", {"text": "part"})
        emitted.set()
        await gate.wait()
        yield event(
            "done",
            {
                "answer": "part complete",
                "sources": [{"source_name": "proof.pdf", "content": "evidence"}],
                "trace": [{"stage": "generation", "label": "生成", "status": "ok"}],
            },
        )

    monkeypatch.setattr(routes_qa, "_sse_events", stream)
    first = await jobs.create(job_request)
    await emitted.wait()
    # No subscriber exists while the job continues.
    repeated = await jobs.create(job_request)
    assert first["turn_id"] == repeated["turn_id"] and calls == 1
    snap = await jobs.read(job_request.session_id, job_request.turn_id)
    assert snap["answer"] == "part"
    with pytest.raises(HTTPException) as e:
        routes_sessions.update_turn(
            job_request.session_id,
            job_request.turn_id,
            routes_sessions.UpdateTurnRequest(answer="stale"),
        )
    assert e.value.status_code == 409
    task = jobs._live[(job_request.session_id, job_request.turn_id)]["task"]
    gate.set()
    await task
    assert (
        db.get_turn(job_request.session_id, job_request.turn_id)["answer"]
        == "part complete"
    )
    assert (await jobs.read(job_request.session_id, job_request.turn_id))[
        "state"
    ] == "done"
    restored = await jobs.read(job_request.session_id, job_request.turn_id)
    assert restored["sources"][0]["source_name"] == "proof.pdf"
    assert restored["trace"][0]["stage"] == "generation"
    await jobs.create(job_request)
    assert calls == 1  # Completed turns never cause a second paid job_request.


@pytest.mark.asyncio
async def test_stop_and_conflicting_request(job_request, monkeypatch):
    entered = asyncio.Event()
    closed = asyncio.Event()

    async def stream(req):
        try:
            yield event("token", {"text": "keep"})
            entered.set()
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(routes_qa, "_sse_events", stream)
    await jobs.create(job_request)
    await entered.wait()
    with pytest.raises(HTTPException) as e:
        await jobs.create(job_request.model_copy(update={"top_k": 3}))
    assert e.value.status_code == 409
    await jobs.stop(job_request.session_id)
    assert closed.is_set()
    result = await jobs.read(job_request.session_id, job_request.turn_id)
    assert result["state"] == "cancelled" and result["answer"] == "keep"
    assert db.get_turn(job_request.session_id, job_request.turn_id)["answer"] == "keep"


@pytest.mark.asyncio
async def test_restart_marks_partial_and_does_not_restart_model(
    job_request, monkeypatch
):
    async def stream(req):
        yield event("token", {"text": "saved"})
        await asyncio.Event().wait()

    monkeypatch.setattr(routes_qa, "_sse_events", stream)
    await jobs.create(job_request)
    await asyncio.sleep(0.05)
    await jobs.shutdown()
    result = await jobs.read(job_request.session_id, job_request.turn_id)
    assert result["state"] == "interrupted"
    # Simulate an unclean exit's persisted running row.
    with db.get_cursor() as c:
        c.execute(
            "UPDATE answer_jobs SET state='running' WHERE session_id=?",
            (job_request.session_id,),
        )
    jobs.initialize()
    result = await jobs.read(job_request.session_id, job_request.turn_id)
    assert result["state"] == "interrupted" and result["answer"] == "saved"
    assert not jobs._live


@pytest.mark.asyncio
async def test_one_task_per_session_and_global_limit(job_request, monkeypatch):
    async def stream(req):
        yield event("token", {"text": "a"})
        await asyncio.Event().wait()

    monkeypatch.setattr(routes_qa, "_sse_events", stream)
    await jobs.create(job_request)
    await asyncio.sleep(0.02)
    db.add_turn(
        session_id=job_request.session_id,
        turn_id="t2",
        order_idx=None,
        question="hello",
        answer=None,
        sources_json="[]",
        trace_json="[]",
        used_provider=None,
        liked=False,
        error=None,
        created_at=2,
    )
    with pytest.raises(HTTPException) as e:
        await jobs.create(job_request.model_copy(update={"turn_id": "t2"}))
    assert e.value.status_code == 409
    await jobs.stop(job_request.session_id)


@pytest.mark.asyncio
async def test_foreign_user_cannot_read_or_stop(job_request, monkeypatch):
    monkeypatch.setattr(accounts, "enabled", True)

    def denied(*args):
        raise HTTPException(404, "not owner")

    monkeypatch.setattr(accounts, "require_owner", denied)
    for operation in (
        jobs.create(job_request),
        jobs.read(job_request.session_id, "t1"),
        jobs.stop(job_request.session_id),
    ):
        with pytest.raises(HTTPException) as e:
            await operation
        assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_stops_task_before_deleting_turn(job_request, monkeypatch):
    async def stream(req):
        yield event("token", {"text": "a"})
        await asyncio.Event().wait()

    monkeypatch.setattr(routes_qa, "_sse_events", stream)
    await jobs.create(job_request)
    await asyncio.sleep(0.02)
    await routes_sessions.delete_turn(job_request.session_id, job_request.turn_id)
    assert db.get_turn(job_request.session_id, job_request.turn_id) is None
    assert not jobs._live


@pytest.mark.asyncio
async def test_stop_before_create_never_calls_model(job_request, monkeypatch):
    def forbidden(*args):
        pytest.fail("cancelled intent started a model")

    monkeypatch.setattr(routes_qa, "_sse_events", forbidden)
    await jobs.stop(job_request.session_id, job_request.turn_id)
    assert (await jobs.create(job_request))["state"] == "cancelled"
    assert not jobs._live


@pytest.mark.asyncio
async def test_revocation_while_model_silent_stops_task(job_request, monkeypatch):
    entered = asyncio.Event()

    async def stream(req):
        entered.set()
        await asyncio.Event().wait()
        yield b""

    monkeypatch.setattr(routes_qa, "_sse_events", stream)
    await jobs.create(job_request)
    await entered.wait()
    task = jobs._live[(job_request.session_id, job_request.turn_id)]["task"]

    def revoked(*args):
        raise HTTPException(403, "permission revoked")

    monkeypatch.setattr(jobs, "check", revoked)
    await asyncio.wait_for(task, 3)
    assert (
        jobs.stored(job_request.session_id, job_request.turn_id)["snapshot"]["state"]
        == "failed"
    )
    with pytest.raises(HTTPException):
        await jobs.read(job_request.session_id, job_request.turn_id)


@pytest.mark.asyncio
async def test_deadline_cancels_real_sse_producer(job_request, monkeypatch):
    closed = asyncio.Event()

    async def model(**kwargs):
        try:
            yield {"type": "token", "data": {"text": "partial"}}
            await asyncio.Event().wait()
        finally:
            closed.set()

    # Use real SSE adapter; basic avoids conversation summary/model setup.
    monkeypatch.setattr(routes_qa, "ask_stream", model)
    monkeypatch.setattr(jobs, "limits", lambda: (0.1, 4))
    job_request.mode = "basic"
    await jobs.create(job_request)
    await jobs._live[(job_request.session_id, job_request.turn_id)]["task"]
    snap = await jobs.read(job_request.session_id, job_request.turn_id)
    assert closed.is_set()
    assert snap["state"] == "failed" and snap["answer"] == "partial"
    assert "总时限" in snap["error"]


@pytest.mark.asyncio
async def test_http_resume_does_not_require_another_post(job_request, monkeypatch):
    import httpx
    from fastapi import FastAPI

    entered, release = asyncio.Event(), asyncio.Event()

    async def stream(req):
        yield event("token", {"text": "first"})
        entered.set()
        await release.wait()
        yield event("done", {"answer": "first second"})

    monkeypatch.setattr(routes_qa, "_sse_events", stream)
    app = FastAPI()
    app.include_router(routes_qa.router)
    path = f"/api/v1/qa/jobs/{job_request.session_id}/{job_request.turn_id}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post("/api/v1/qa/jobs", json=job_request.model_dump())
        assert result.status_code == 200
        await entered.wait()
    # New HTTP client represents a fresh page, with no prior SSE connection.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        snapshot = await client.get(path)
        assert snapshot.json()["answer"] == "first"
        task = jobs._live[(job_request.session_id, job_request.turn_id)]["task"]
        release.set()
        await task
        final = (await client.get(path)).json()
        assert final["state"] == "done" and final["answer"] == "first second"


@pytest.mark.asyncio
async def test_global_concurrency_rejects_without_starting_extra_job(
    job_request, monkeypatch
):
    async def stream(req):
        yield event("token", {"text": "a"})
        await asyncio.Event().wait()

    monkeypatch.setattr(routes_qa, "_sse_events", stream)
    monkeypatch.setattr(jobs, "limits", lambda: (60, 1))
    await jobs.create(job_request)
    await asyncio.sleep(0.02)
    sid = uuid.uuid4().hex
    db.create_session(sid, "other", 1, 1, kb_scope="default")
    db.add_turn(sid, sid + "-t", None, "hello", None, "[]", "[]", None, False, None, 1)
    try:
        with pytest.raises(HTTPException) as error:
            await jobs.create(
                job_request.model_copy(
                    update={"session_id": sid, "turn_id": sid + "-t"}
                )
            )
        assert error.value.status_code == 429
        assert jobs.stored(sid, sid + "-t") is None
    finally:
        await jobs.stop(job_request.session_id)


@pytest.mark.asyncio
async def test_immediate_stop_does_not_leave_running_row(job_request, monkeypatch):
    async def stream(req):
        await asyncio.Event().wait()
        yield b""

    monkeypatch.setattr(routes_qa, "_sse_events", stream)
    await jobs.create(job_request)
    await jobs.stop(job_request.session_id, job_request.turn_id)
    assert not jobs._live
    assert (await jobs.read(job_request.session_id, job_request.turn_id))[
        "state"
    ] == "cancelled"


def test_elapsed_ignores_wall_clock_skew(monkeypatch):
    job = {"clock_start": 100.0, "snapshot": {"state": "running", "started_at": 1000}}
    monkeypatch.setattr(jobs.time, "monotonic", lambda: 102.5)
    monkeypatch.setattr(jobs.time, "time", lambda: 999999)
    assert jobs.response_snapshot(job)["elapsed_ms"] == 2500
    job["snapshot"]["state"] = "done"
    monkeypatch.setattr(jobs.time, "monotonic", lambda: 800)
    assert jobs.response_snapshot(job)["elapsed_ms"] == 2500


def test_legacy_finished_duration_and_restart_checkpoint(monkeypatch):
    job = {"snapshot": {"state": "done", "started_at": 1000, "finished_at": 4500}}
    assert jobs.response_snapshot(job)["elapsed_ms"] == 3500
    job = {"snapshot": {"state": "running", "started_at": 1000,
                        "elapsed_ms": 2500, "revision": 0, "trace": []}}
    monkeypatch.setattr(jobs.time, "time", lambda: 999999)
    jobs.mark_interrupted(job)
    assert job["snapshot"]["elapsed_ms"] == 2500
