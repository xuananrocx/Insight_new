"""会话历史 API 路由。

会话持久化在用户数据目录的 metadata.db（SQLite）。
字段裁剪：sources.content 截到 200 字；trace.candidates.preview 截到 80 字。
"""
from __future__ import annotations

import io
import json
import logging
import re
import time
import zipfile
from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, UploadFile, File, Response
from pydantic import BaseModel, Field, field_validator

from src.db import metadata_db
from src.core.retrieval_modes import RetrievalMode, VALID_MODES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


# ===== 字段裁剪 =====

SOURCE_SNIPPET_MAX = 200
CANDIDATE_PREVIEW_MAX = 80


def _trim_sources(sources: list[dict] | None, long_content: bool = False) -> list[dict]:
    """裁剪 sources：保留必要字段。content 截 200 字；检索结果消息（long_content）截 6000 字并保留 score_pct/merged_chunks。"""
    if not sources:
        return []
    content_max = 6000 if long_content else SOURCE_SNIPPET_MAX
    out = []
    for s in sources:
        if not isinstance(s, dict):
            continue
        item = {
            "source_path": s.get("source_path") or s.get("file_path") or s.get("rel_path") or "",
            "source_name": s.get("source_name") or "",
            "title": s.get("title") or "",
            "section_label": s.get("section_label") or "",
            "file_type": s.get("file_type") or "",
            "score": s.get("score") if isinstance(s.get("score"), (int, float)) else None,
        }
        if isinstance(s.get("score_pct"), (int, float)):
            item["score_pct"] = int(s["score_pct"])
        if isinstance(s.get("merged_chunks"), int):
            item["merged_chunks"] = s["merged_chunks"]
        # Preserve the evidence snapshot identity when a document is later changed or removed.
        for key in ("citation_id", "document_id", "section_index", "offset", "page"):
            if isinstance(s.get(key), (str, int)):
                item[key] = str(s[key])[:128] if isinstance(s[key], str) else s[key]
        if isinstance(s.get("content_hash"), str):
            item["content_hash"] = s["content_hash"][:128]
        if isinstance(s.get("chunk_ids"), list):
            item["chunk_ids"] = [c[:200] for c in s["chunk_ids"][:10000] if isinstance(c, str)]
        if isinstance(s.get('reading'), dict):
            item['reading'] = {k: v for k, v in s['reading'].items() if k in {
                'kind', 'offset_basis', 'start', 'end', 'truncated', 'budget_truncated', 'section_complete', 'total_chars', 'index_incomplete', 'prefix_omitted', 'artifact', 'object_name', 'boundary_known', 'object_complete', 'returned_to_end', 'remaining_chars'
            } and isinstance(v, (str, int, bool, type(None)))}
        content = s.get("content") or s.get("text_snippet") or ""
        if isinstance(content, str):
            item["content"] = content[:content_max]
        out.append(item)
    return out


def _trim_trace(trace: list[dict] | None) -> list[dict]:
    """裁剪 trace：保留 stage 元数据，candidates.preview 截到 80 字。"""
    if not trace:
        return []
    out = []
    for st in trace:
        if not isinstance(st, dict):
            continue
        item = {
            "stage": st.get("stage") or "",
            "label": st.get("label") or "",
            "status": st.get("status") or "ok",
            "count": st.get("count"),
            "duration_ms": st.get("duration_ms"),
            "notes": st.get("notes"),
        }
        if st.get('stage') == 'conversation_context' and isinstance(st.get('context'), dict):
            item['context'] = {k: v for k, v in st['context'].items() if k in {
                'candidate_turns', 'recent_turns', 'summary_turns', 'summary_version', 'preferences_loaded',
                'preferences_version', 'budget_tokens', 'estimated_tokens', 'token_count_kind', 'clipped',
                'omitted_turns', 'summary_state', 'degraded', 'request_id', 'history_reads', 'duration_ms',
                'history_tokens', 'summary_tokens', 'preferences_tokens', 'question_tokens', 'context_window_tokens', 'model_input_tokens'
            } and isinstance(v, (str, int, float, bool, type(None)))}
        if st.get('stage') == 'evidence_summary':
            for key in ('stop_reason', 'tool_calls', 'evidence_chars', 'reviewed_points', 'unresolved_points'):
                if isinstance(st.get(key), (str, int)):
                    item[key] = st[key]
        cands = st.get("candidates") or []
        if isinstance(cands, list):
            trimmed_cands = []
            for c in cands:
                if not isinstance(c, dict):
                    continue
                tc = {
                    "source_name": c.get("source_name") or c.get("title") or "",
                    "title": c.get("title") or "",
                    "score": c.get("score") if isinstance(c.get("score"), (int, float)) else None,
                    "score_type": c.get("score_type") or "",
                }
                preview = c.get("preview") or ""
                if isinstance(preview, str):
                    tc["preview"] = preview[:CANDIDATE_PREVIEW_MAX]
                trimmed_cands.append(tc)
            item["candidates"] = trimmed_cands
        out.append(item)
    return out


def _normalize_turn(turn: dict) -> dict:
    """规范化 turn 字段。"""
    return {
        "id": turn.get("id") or "",
        "order_idx": turn.get("order_idx", 0),
        "question": turn.get("question") or "",
        "answer": turn.get("answer"),
        "sources": turn.get("sources") or [],
        "trace": turn.get("trace") or [],
        "used_provider": turn.get("used_provider"),
        "liked": bool(turn.get("liked", False)),
        "error": turn.get("error"),
        "created_at": turn.get("created_at") or 0,
        "mode": turn.get("mode") or "ai",
        "expansion": turn.get("expansion"),
    }


# ===== Request/Response Models =====

class CreateSessionRequest(BaseModel):
    group_id: str | None = None
    id: str = Field(..., min_length=1, max_length=64)
    title: str = Field("新会话", max_length=80)
    created_at: int = Field(..., ge=0)
    kb_scope: str | None = None
    retrieval_mode: RetrievalMode = "ai"


class UpdateSessionRequest(BaseModel):
    group_id: str | None = None
    automatic_title: bool = False

    @field_validator("title")
    @classmethod
    def valid_title(cls, value):
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("会话名称不能为空")
        return value

    title: str | None = Field(None, max_length=80)
    kb_scope: str | None = None
    retrieval_mode: RetrievalMode | None = None


class TurnModel(BaseModel):
    id: str
    order_idx: int
    question: str
    answer: str | None = None
    sources: list[dict] = Field(default_factory=list)
    trace: list[dict] = Field(default_factory=list)
    used_provider: str | None = None
    liked: bool = False
    error: str | None = None
    created_at: int
    mode: RetrievalMode = "ai"
    expansion: dict | None = None


class SessionSummary(BaseModel):
    group_id: str | None = None
    title_source: str = "manual"
    id: str
    title: str
    created_at: int
    updated_at: int
    turn_count: int
    kb_scope: str | None = None
    retrieval_mode: RetrievalMode = "ai"


class SessionDetail(SessionSummary):
    turns: list[TurnModel]


class AddTurnRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    question: str = Field(..., min_length=1)
    answer: str | None = None
    sources: list[dict] = Field(default_factory=list)
    trace: list[dict] = Field(default_factory=list)
    used_provider: str | None = None
    liked: bool = False
    error: str | None = None
    created_at: int = Field(..., ge=0)
    mode: RetrievalMode | None = None


class UpdateTurnRequest(BaseModel):
    answer: str | None = None
    sources: list[dict] | None = None
    trace: list[dict] | None = None
    used_provider: str | None = None
    liked: bool | None = None
    error: str | None = None
    updated_at: int | None = None


# ===== Endpoints =====

from src.db import session_groups


class GroupRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


class GroupUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    direction: Literal["up", "down"] | None = None


@router.get("/groups")
def list_groups():
    return session_groups.list_groups()


@router.post("/groups", status_code=201)
def create_group(req: GroupRequest):
    return session_groups.create(req.name)


@router.patch("/groups/{group_id}")
def update_group(group_id: str, req: GroupUpdate):
    session_groups.change(group_id, req.name, req.direction)
    return {"ok": True}


@router.delete("/groups/{group_id}")
def delete_group(group_id: str):
    session_groups.change(group_id, delete=True)
    return {"ok": True}


@router.get("", response_model=list[SessionSummary])
def list_sessions(limit: int = 200) -> list[dict]:
    return metadata_db.list_sessions(limit=limit)


@router.get("/{session_id}", response_model=SessionDetail)
def get_session(session_id: str) -> dict:
    s = metadata_db.get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")
    return s


@router.post("", response_model=SessionSummary, status_code=201)
def create_session(req: CreateSessionRequest) -> dict:
    try:
        metadata_db.create_session(
            session_id=req.id,
            title=req.title,
            created_at=req.created_at,
            updated_at=req.created_at,
            kb_scope=req.kb_scope,
            retrieval_mode=req.retrieval_mode,
            group_id=req.group_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if "UNIQUE constraint failed" in msg:
            raise HTTPException(status_code=409, detail="会话 ID 已存在")
        raise HTTPException(status_code=500, detail=f"创建失败: {e}")
    logger.info(f"create session id={req.id} mode={req.retrieval_mode}")
    return {
        "id": req.id,
        "title": req.title,
        "created_at": req.created_at,
        "updated_at": req.created_at,
        "turn_count": 0,
        "kb_scope": req.kb_scope,
        "retrieval_mode": req.retrieval_mode,
        "group_id": req.group_id,
        "title_source": "default" if req.title == "新会话" else "manual",
    }


@router.patch("/{session_id}", response_model=SessionSummary)
def update_session(session_id: str, req: UpdateSessionRequest) -> dict:
    import time
    # 只更新用户显式传入的字段（pydantic exclude_unset=True）
    update_fields = req.model_dump(exclude_unset=True)
    # 总是刷新 updated_at
    update_fields["updated_at"] = int(time.time() * 1000)
    ok = metadata_db.update_session(session_id=session_id, **update_fields)
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    s = metadata_db.get_session(session_id)
    return {k: v for k, v in s.items() if k != "turns"}


@router.delete("/{session_id}", status_code=204, response_model=None)
def delete_session(session_id: str) -> None:
    ok = metadata_db.delete_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")


@router.post("/{session_id}/turns", response_model=TurnModel, status_code=201)
def add_turn(session_id: str, req: AddTurnRequest) -> dict:
    if not metadata_db.get_session(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")

    sources_trimmed = _trim_sources(req.sources, long_content=req.mode in ("basic", "deep", "deep_ai"))
    trace_trimmed = _trim_trace(req.trace)
    mode = req.mode or "ai"

    # 让 metadata_db 自己算 order_idx（并发安全，避免 race condition）
    ok = metadata_db.add_turn(
        session_id=session_id,
        turn_id=req.id,
        order_idx=None,
        question=req.question,
        answer=req.answer,
        sources_json=json.dumps(sources_trimmed, ensure_ascii=False),
        trace_json=json.dumps(trace_trimmed, ensure_ascii=False),
        used_provider=req.used_provider,
        liked=req.liked,
        error=req.error,
        created_at=req.created_at,
        mode=mode,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 回读 order_idx 给前端
    turn = metadata_db.get_turn(session_id, req.id)
    order_idx = turn["order_idx"] if turn else 0

    logger.info(
        f"add turn sid={session_id} tid={req.id} question_len={len(req.question)} "
        f"answer_len={len(req.answer) if req.answer else 0} mode={mode}"
    )

    return {
        "id": req.id,
        "order_idx": order_idx,
        "question": req.question,
        "answer": req.answer,
        "sources": sources_trimmed,
        "trace": trace_trimmed,
        "used_provider": req.used_provider,
        "liked": req.liked,
        "error": req.error,
        "created_at": req.created_at,
        "mode": mode,
    }


@router.patch("/{session_id}/turns/{turn_id}", response_model=dict)
def update_turn(session_id: str, turn_id: str, req: UpdateTurnRequest) -> dict:
    # 只更新用户显式传入的字段（pydantic exclude_unset=True）
    update_fields = req.model_dump(exclude_unset=True)

    # sources 和 trace 需要序列化为 JSON 字符串
    if "sources" in update_fields:
        turn = metadata_db.get_turn(session_id, turn_id)
        long_content = bool(turn and turn.get("mode") in ("basic", "deep", "deep_ai"))
        update_fields["sources_json"] = json.dumps(
            _trim_sources(update_fields.pop("sources"), long_content=long_content), ensure_ascii=False
        )
    if "trace" in update_fields:
        update_fields["trace_json"] = json.dumps(
            _trim_trace(update_fields.pop("trace")), ensure_ascii=False
        )

    if not update_fields:
        raise HTTPException(status_code=400, detail="没有要更新的字段")

    ok = metadata_db.update_turn(
        session_id=session_id,
        turn_id=turn_id,
        **update_fields,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="turn 不存在")
    logger.info(
        f"patch turn sid={session_id} tid={turn_id} "
        f"answer_len={len(req.answer) if req.answer else 'null'}"
    )
    return {"ok": True}


@router.delete("/{session_id}/turns/{turn_id}", status_code=204, response_model=None)
def delete_turn(session_id: str, turn_id: str) -> None:
    ok = metadata_db.delete_turn(session_id, turn_id)
    if not ok:
        raise HTTPException(status_code=404, detail="turn 不存在")


@router.delete("/{session_id}/turns", status_code=200, response_model=None)
def clear_all_turns(session_id: str) -> dict:
    """清空某会话所有 turns（用于切 KB 时旧 turns 失效）。

    场景：用户在 KB-A 上有几轮问答 → 切到 KB-B → 旧的 turns 答案跟新 KB 没关系，
    会造成用户困惑。前端在切 KB 时调此端点，同步清空后端 turns。
    """
    if not metadata_db.get_session(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    deleted = metadata_db.delete_all_turns(session_id)
    logger.info(f"clear_all_turns sid={session_id} deleted={deleted}")
    return {"deleted": deleted}


# ===== 导出/导入 =====

EXPORT_VERSION = 1


def _sanitize_filename(name: str, max_len: int = 40) -> str:
    """清理文件名：去特殊字符、限制长度。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name or "").strip()
    name = re.sub(r"_+", "_", name).strip("_")
    if not name:
        name = "session"
    # 截短：按字符（中文按 1 字符），避免文件名过长
    if len(name) > max_len:
        name = name[:max_len].rstrip("_")
    return name


def _build_session_payload(session_id: str) -> dict | None:
    """构造单会话导出 payload。"""
    s = metadata_db.get_session(session_id)
    if not s:
        return None
    return {
        "type": "session",
        "version": EXPORT_VERSION,
        "exported_at": int(time.time()),
        "session": {
            "id": s["id"],
            "title": s["title"],
            "created_at": s["created_at"],
            "updated_at": s["updated_at"],
            "kb_scope": s.get("kb_scope"),
            "retrieval_mode": s.get("retrieval_mode", "ai"),
            "group_name": next((g["name"] for g in session_groups.list_groups() if g["id"] == s.get("group_id")), None),
        },
        "turns": s.get("turns", []),
    }


@router.get("/{session_id}/export")
def export_session(session_id: str) -> Response:
    """导出单个会话为 JSON 文件（Content-Disposition 触发浏览器下载）。"""
    payload = _build_session_payload(session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    title = payload["session"]["title"] or "session"
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"{_sanitize_filename(title)}_{date_str}.json"
    # RFC 5987：filename 用 ASCII fallback（latin-1 不支持中文），filename* 用 UTF-8 编码
    ascii_fallback = "session_" + date_str + ".json"
    encoded = quote(filename, safe="")

    body = json.dumps(payload, ensure_ascii=False, indent=2)
    logger.info(f"export session sid={session_id} title={title!r} turns={len(payload['turns'])}")
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}",
        },
    )


class ExportBatchRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1)


@router.post("/export-batch")
def export_batch(req: ExportBatchRequest) -> Response:
    """批量导出会话为 zip 文件。每个会话一个 JSON。"""
    seen_filenames: set[str] = set()
    buf = io.BytesIO()
    success_count = 0
    failed_ids: list[str] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for sid in req.ids:
            payload = _build_session_payload(sid)
            if payload is None:
                failed_ids.append(sid)
                continue
            title = payload["session"]["title"] or "session"
            date_str = datetime.now().strftime("%Y-%m-%d")
            base = f"{_sanitize_filename(title)}_{date_str}"
            filename = f"{base}.json"
            # 同名去重
            n = 2
            while filename in seen_filenames:
                filename = f"{base}_{n}.json"
                n += 1
            seen_filenames.add(filename)
            zf.writestr(filename, json.dumps(payload, ensure_ascii=False, indent=2))
            success_count += 1

    if success_count == 0:
        raise HTTPException(status_code=404, detail="所有会话均不存在")

    date_str = datetime.now().strftime("%Y-%m-%d")
    zip_name = f"sessions_{date_str}.zip"
    ascii_fallback = "sessions_" + date_str + ".zip"
    encoded = quote(zip_name, safe="")
    logger.info(
        f"export batch success={success_count} failed={len(failed_ids)} zip={zip_name}"
    )
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}",
        },
    )


def _gen_new_session_id() -> str:
    """生成新的会话 ID（避免冲突）。"""
    import secrets
    return f"s_imported_{int(time.time() * 1000)}_{secrets.token_hex(4)}"


def _import_one_session(payload: dict) -> dict:
    """导入单个会话。返回 {id, title, turn_count, created_at}。"""
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是对象")
    if payload.get("type") != "session":
        raise ValueError("type 字段必须为 'session'")
    sess = payload.get("session") or {}
    turns = payload.get("turns") or []
    if not sess.get("id") or not sess.get("title"):
        raise ValueError("session.id 和 session.title 必填")

    title = str(sess.get("title", ""))[:80]
    # 加 "(导入)" 后缀（如果原标题没这后缀）
    suffix = " (导入)"
    if not title.endswith(suffix):
        title = title + suffix

    # 时间：导入会话排序到最前 → 用当前时间覆盖 created_at/updated_at
    # 但 turns 的 created_at 保留原值（保持问答时序）
    now_ms = int(time.time() * 1000)
    new_id = _gen_new_session_id()

    group_id = None
    if isinstance(sess.get("group_name"), str) and sess["group_name"].strip():
        group_name = sess["group_name"].strip()[:80]
        group = next((g for g in session_groups.list_groups() if g["name"] == group_name), None)
        group_id = (group or session_groups.create(group_name))["id"]
    try:
        metadata_db.create_session(
            group_id=group_id,
            session_id=new_id,
            title=title,
            created_at=now_ms,
            updated_at=now_ms,
            kb_scope=sess.get("kb_scope"),
            retrieval_mode=sess.get("retrieval_mode") if sess.get("retrieval_mode") in VALID_MODES else "ai",
        )
    except Exception as e:
        raise ValueError(f"创建会话失败: {e}")

    # 导入 turns（用新 session_id 派生 turn_id 避免冲突）
    imported_turn_count = 0
    for idx, t in enumerate(turns):
        if not isinstance(t, dict):
            continue
        try:
            sources = t.get("sources") or []
            trace = t.get("trace") or []
            metadata_db.add_turn(
                session_id=new_id,
                turn_id=f"{new_id}_t{idx}",
                order_idx=idx,
                question=str(t.get("question") or ""),
                answer=t.get("answer"),
                sources_json=json.dumps(sources, ensure_ascii=False),
                trace_json=json.dumps(trace, ensure_ascii=False),
                used_provider=t.get("used_provider"),
                liked=bool(t.get("liked", False)),
                error=t.get("error"),
                created_at=int(t.get("created_at") or now_ms),
                mode=t.get("mode") if t.get("mode") in VALID_MODES else "ai",
            )
            if isinstance(t.get("expansion"), dict):
                expansion = t["expansion"]
                metadata_db.update_turn(new_id, f"{new_id}_t{idx}", expansion_json=json.dumps({
                    "sources": _trim_sources(expansion.get("sources"), long_content=True),
                    "trace": _trim_trace(expansion.get("trace")),
                    "completed_at": int(expansion.get("completed_at") or now_ms),
                }, ensure_ascii=False))
            imported_turn_count += 1
        except Exception as e:
            logger.warning(f"import turn {idx} failed: {e}")
            continue

    logger.info(
        f"import session new_id={new_id} title={title!r} turns={imported_turn_count}"
    )
    return {
        "id": new_id,
        "title": title,
        "turn_count": imported_turn_count,
        "created_at": now_ms,
    }


class ImportResponse(BaseModel):
    imported: int = Field(..., description="成功导入的会话数")
    sessions: list[dict] = Field(default_factory=list, description="每个会话的 {id, title, turn_count}")
    errors: list[str] = Field(default_factory=list, description="每个失败项的错误信息")


@router.post("/import", response_model=ImportResponse)
async def import_sessions(file: UploadFile = File(...)) -> ImportResponse:
    """导入会话文件（.json 单会话 或 .zip 批量）。

    - ID 冲突时生成新 ID
    - 标题自动加 "(导入)" 后缀
    - 导入会话的时间戳设为当前 → 排序到列表最前
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="上传文件为空")

    filename = (file.filename or "").lower()
    payloads: list[dict] = []
    errors: list[str] = []

    try:
        if filename.endswith(".zip") or raw[:4] == b"PK\x03\x04":
            # zip 批量
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                for member in zf.namelist():
                    if not member.endswith(".json"):
                        continue
                    try:
                        with zf.open(member) as f:
                            data = json.loads(f.read().decode("utf-8"))
                        payloads.append(data)
                    except Exception as e:
                        errors.append(f"{member}: 解析失败 ({e})")
        else:
            # 单 JSON
            try:
                payloads.append(json.loads(raw.decode("utf-8")))
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"JSON 解析失败: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件读取失败: {e}")

    sessions: list[dict] = []
    for p in payloads:
        try:
            result = _import_one_session(p)
            sessions.append(result)
        except Exception as e:
            errors.append(f"会话导入失败: {e}")

    logger.info(
        f"import file={filename!r} payloads={len(payloads)} success={len(sessions)} errors={len(errors)}"
    )
    return ImportResponse(imported=len(sessions), sessions=sessions, errors=errors)
