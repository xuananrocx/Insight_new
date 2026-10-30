"""Single-worker, server-owned answer jobs. Connections only read snapshots.

Rows are additive; final answers remain in the existing turns table. No model
credentials or request history are persisted. Never restart paid work on boot.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from contextlib import suppress

from fastapi import HTTPException
from src.core import accounts
from src.core.config import settings
from src.db import metadata_db as db

log = logging.getLogger(__name__)
_live: dict[tuple[str, str], dict] = {}
_lock = asyncio.Lock()


def update_elapsed(job):
    """Elapsed duration uses one clock; never compare server/client timestamps."""
    snap = job["snapshot"]
    if "clock_start" in job:
        snap["elapsed_ms"] = max(0, int((time.monotonic() - job["clock_start"]) * 1000))
    elif "elapsed_ms" not in snap:
        end = snap.get("finished_at", int(time.time() * 1000))
        snap["elapsed_ms"] = max(0, end - snap["started_at"])


def response_snapshot(job):
    if job["snapshot"]["state"] == "running" or "elapsed_ms" not in job["snapshot"]:
        update_elapsed(job)
    return json.loads(json.dumps(job["snapshot"]))


def limits():
    cfg = settings.config.get("qa", {}).get("ai_generation", {})
    return (
        max(30, min(86400, int(cfg.get("job_timeout_seconds", 3600)))),
        max(1, min(64, int(cfg.get("job_max_concurrent", 4)))),
    )


def initialize():
    with db.get_cursor() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS answer_jobs (
            session_id TEXT NOT NULL, turn_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            kb_id TEXT NOT NULL, state TEXT NOT NULL, snapshot TEXT NOT NULL,
            PRIMARY KEY(session_id,turn_id))""")
        rows = c.execute("SELECT * FROM answer_jobs WHERE state='running'").fetchall()
        c.execute(
            "DELETE FROM answer_jobs WHERE NOT EXISTS (SELECT 1 FROM turns WHERE turns.session_id=answer_jobs.session_id AND turns.id=answer_jobs.turn_id)"
        )
    for row in rows:
        job = dict(row)
        job = stored(job["session_id"], job["turn_id"])
        if not job:
            continue
        mark_interrupted(job)
        save(job)


def mark_interrupted(job):
    snap = job["snapshot"]
    update_elapsed(job)
    snap.update(
        state="interrupted",
        error="服务重启，生成中断；已保留部分回答",
        finished_at=int(time.time() * 1000),
    )
    snap["trace"] = [
        s
        for s in snap.get("trace", [])
        if s.get("status") != "running" and s.get("stage") != "answer_result"
    ]
    snap["trace"].append(
        dict(
            stage="answer_result", label="回答中断", status="error", notes=snap["error"]
        )
    )
    snap["revision"] += 1


def check(session_id, kb_id=None):
    if accounts.enabled:
        accounts.require_owner("session", session_id)
    with db.get_cursor() as c:
        row = c.execute(
            "SELECT id,kb_scope FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
    session = dict(row) if row else None
    if not session:
        raise HTTPException(404, "会话不存在")
    if kb_id is not None and session.get("kb_scope") != kb_id:
        raise HTTPException(409, "会话知识库已改变")
    if accounts.enabled:
        accounts.require_kb(kb_id or session.get("kb_scope"))
    return session


def stored(session_id, turn_id):
    with db.get_cursor() as c:
        row = c.execute(
            "SELECT * FROM answer_jobs WHERE session_id=? AND turn_id=?",
            (session_id, turn_id),
        ).fetchone()
    if not row:
        return None
    job = dict(row)
    job["snapshot"] = json.loads(job["snapshot"])
    if "answer" not in job["snapshot"]:
        turn = db.get_turn(session_id, turn_id) or {}
        job["snapshot"].update(
            answer=turn.get("answer") or "",
            sources=json.loads(turn.get("sources_json") or "[]"),
            trace=json.loads(turn.get("trace_json") or "[]"),
        )
    return job


def save(job):
    from src.api.routes_sessions import _trim_sources, _trim_trace

    snap = job["snapshot"]
    # Serialize before entering the DB transaction; caller passes an owned copy.
    persisted = (
        snap
        if snap["state"] == "running"
        else {k: v for k, v in snap.items() if k not in ("answer", "sources", "trace")}
    )
    encoded = json.dumps(persisted, ensure_ascii=False)
    with db.get_cursor() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute(
            "UPDATE answer_jobs SET state=?,snapshot=? WHERE session_id=? AND turn_id=?",
            (snap["state"], encoded, job["session_id"], job["turn_id"]),
        )
        c.execute(
            "UPDATE turns SET answer=?,sources_json=?,trace_json=?,error=?,used_provider=? WHERE session_id=? AND id=?",
            (
                snap["answer"],
                json.dumps(
                    _trim_sources(snap["sources"], long_content=True),
                    ensure_ascii=False,
                ),
                json.dumps(_trim_trace(snap["trace"]), ensure_ascii=False),
                snap.get("error"),
                snap.get("used_provider"),
                job["session_id"],
                job["turn_id"],
            ),
        )
        c.execute("COMMIT")


def copy_job(job):
    return {
        k: json.loads(json.dumps(v))
        for k, v in job.items()
        if k not in ("task", "wake")
    }


async def checkpoint(job):
    # SQLite work cannot be cancelled mid-thread: drain it before a final save,
    # otherwise an older checkpoint could overwrite a cancelled/completed answer.
    update_elapsed(job)
    task = asyncio.create_task(asyncio.to_thread(save, copy_job(job)))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def create(req):
    await asyncio.to_thread(check, req.session_id, req.kb_scope)
    turn = await asyncio.to_thread(db.get_turn, req.session_id, req.turn_id)
    if not turn or turn["question"] != req.question:
        raise HTTPException(409, "请先保存对应问题，再开始生成")
    fingerprint = hashlib.sha256(req.model_dump_json().encode()).hexdigest()
    key = (req.session_id, req.turn_id)
    async with _lock:
        existing = await asyncio.to_thread(stored, *key)
        if existing:
            if existing["fingerprint"] and existing["fingerprint"] != fingerprint:
                raise HTTPException(409, "此问题已创建生成任务，请恢复任务或新建问题")
            return response_snapshot(_live.get(key) or existing)
        if turn.get("answer"):
            raise HTTPException(409, "此问题已有回答，请新建问题重新生成")
        if any(k[0] == req.session_id for k in _live):
            raise HTTPException(409, "该会话正在生成，请先停止或等待完成")
        await asyncio.to_thread(check, req.session_id, req.kb_scope)
        if not await asyncio.to_thread(db.get_turn, *key):
            raise HTTPException(404, "问题已删除")
        timeout, maximum = limits()
        if len(_live) >= maximum:
            raise HTTPException(429, "同时生成的任务已达上限，请稍后重试")
        snap = dict(
            session_id=req.session_id,
            turn_id=req.turn_id,
            state="running",
            revision=0,
            answer="",
            sources=[],
            trace=[],
            started_at=int(time.time() * 1000),
            elapsed_ms=0,
            error=None,
        )
        job = dict(
            session_id=req.session_id,
            turn_id=req.turn_id,
            kb_id=req.kb_scope,
            fingerprint=fingerprint,
            snapshot=snap,
            clock_start=time.monotonic(),
        )

        def insert():
            with db.get_cursor() as c:
                c.execute(
                    "INSERT INTO answer_jobs VALUES (?,?,?,?,?,?)",
                    (*key, fingerprint, req.kb_scope, "running", json.dumps(snap)),
                )

        await asyncio.to_thread(insert)
        _live[key] = job
        job["task"] = asyncio.create_task(run(job, req, timeout))
        # Install the worker cleanup before exposing it to stop/shutdown.
        await asyncio.sleep(0)
        return response_snapshot(job)


async def run(job, req, timeout):
    from src.api.routes_qa import _sse_events

    snap = job["snapshot"]
    stream = _sse_events(req)
    last_save = 0.0
    saved_revision = -1
    pending = None
    try:
        async with asyncio.timeout(timeout):
            while snap["state"] == "running":
                if pending is None:
                    pending = asyncio.create_task(stream.__anext__())
                done, _ = await asyncio.wait({pending}, timeout=1)
                # Check even while a model is silent, not just when it emits.
                await asyncio.to_thread(check, req.session_id, req.kb_scope)
                if not await asyncio.to_thread(
                    db.get_turn, req.session_id, req.turn_id
                ):
                    raise RuntimeError("问题已删除，停止生成")
                if done:
                    try:
                        block = pending.result().decode()
                    except StopAsyncIteration:
                        raise RuntimeError("生成结束但未返回完成状态") from None
                    pending = None
                    if block.startswith("event: "):
                        kind, payload = block.split("\ndata: ", 1)
                        data = json.loads(payload.strip())
                        kind = kind.removeprefix("event: ")
                        if kind == "token":
                            snap["answer"] += data.get("text", "")
                        elif kind == "sources":
                            snap["sources"] = data
                        elif kind == "stage":
                            snap["trace"] = [
                                s
                                for s in snap["trace"]
                                if not (
                                    s.get("stage") == data.get("stage")
                                    and s.get("status") == "running"
                                )
                            ]
                            snap["trace"].append(data)
                            snap["trace"] = snap["trace"][-500:]
                        elif kind == "done":
                            snap.update(
                                answer=data.get("answer", snap["answer"]),
                                sources=data.get("sources", snap["sources"]),
                                trace=data.get("trace", snap["trace"]),
                                used_provider=data.get("used_provider"),
                                outcome=data.get("outcome"),
                                verification=data.get("verification"),
                                state="done",
                            )
                        elif kind == "error":
                            snap.update(
                                state="failed", error=data.get("message", "生成失败")
                            )
                            if data.get("partial"):
                                snap["answer"] = data["partial"]
                        snap["revision"] += 1
                if len(snap["answer"]) > 4_000_000:
                    raise RuntimeError("回答超过安全存储上限，已停止生成")
                if (
                    snap["revision"] != saved_revision
                    and time.monotonic() - last_save >= 1
                ):
                    saved_revision = snap["revision"]
                    await checkpoint(job)
                    last_save = time.monotonic()
    except asyncio.CancelledError:
        snap.update(state="cancelled", error="已停止生成")
    except TimeoutError:
        snap.update(
            state="failed", error=f"生成任务达到总时限（{timeout} 秒），已保留部分回答"
        )
    except Exception as exc:
        log.exception(
            "answer job failed session=%s turn=%s", req.session_id, req.turn_id
        )
        snap.update(state="failed", error=str(getattr(exc, "detail", exc)))
    finally:
        if pending:
            pending.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await pending
        with suppress(asyncio.CancelledError, Exception):
            await stream.aclose()
        snap["trace"] = [s for s in snap["trace"] if s.get("status") != "running"]
        if snap["state"] != "done":
            snap["trace"].append(
                dict(
                    stage="answer_result",
                    label="已停止回答"
                    if snap["state"] == "cancelled"
                    else "回答未完成",
                    status="cancelled" if snap["state"] == "cancelled" else "error",
                    notes=snap.get("error"),
                )
            )
        snap["finished_at"] = int(time.time() * 1000)
        snap["revision"] += 1
        try:
            await checkpoint(job)
        finally:
            _live.pop((req.session_id, req.turn_id), None)


async def read(session_id, turn_id):
    job = _live.get((session_id, turn_id)) or await asyncio.to_thread(
        stored, session_id, turn_id
    )
    await asyncio.to_thread(check, session_id, job["kb_id"] if job else None)
    if not job:
        raise HTTPException(404, "生成任务不存在")
    return response_snapshot(job)


async def stop(session_id, turn_id=None):
    # Ownership still permits stopping after KB access was revoked.
    if accounts.enabled:
        await asyncio.to_thread(accounts.require_owner, "session", session_id)
    async with _lock:
        jobs = [
            j
            for (sid, tid), j in list(_live.items())
            if sid == session_id and (turn_id is None or tid == turn_id)
        ]
        for job in jobs:
            if job["snapshot"]["state"] == "running":
                job["task"].cancel()
        # A stop may overtake the create HTTP request. Record cancellation intent
        # so that delayed creation cannot start a paid request afterwards.
        if turn_id and not await asyncio.to_thread(stored, session_id, turn_id):
            session = await asyncio.to_thread(db.get_session, session_id)
            if session:
                snap = dict(
                    session_id=session_id,
                    turn_id=turn_id,
                    state="cancelled",
                    revision=1,
                    answer="",
                    sources=[],
                    trace=[],
                    started_at=int(time.time() * 1000),
                    elapsed_ms=0,
                    error="已停止生成",
                )

                def tombstone():
                    with db.get_cursor() as c:
                        c.execute(
                            "INSERT OR IGNORE INTO answer_jobs VALUES (?,?,?,?,?,?)",
                            (
                                session_id,
                                turn_id,
                                "",
                                session.get("kb_scope") or "default",
                                "cancelled",
                                json.dumps(snap),
                            ),
                        )

                await asyncio.to_thread(tombstone)
        if jobs:
            await asyncio.gather(*(j["task"] for j in jobs), return_exceptions=True)


def managed(session_id, turn_id):
    return stored(session_id, turn_id) is not None


async def shutdown():
    all_jobs = list(_live.values())
    interrupted = [j for j in all_jobs if j["snapshot"]["state"] == "running"]
    for job in interrupted:
        job["task"].cancel()
    await asyncio.gather(*(j["task"] for j in all_jobs), return_exceptions=True)
    for job in interrupted:
        mark_interrupted(job)
        await checkpoint(job)
