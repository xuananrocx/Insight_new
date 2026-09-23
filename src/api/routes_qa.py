"""问答相关 API 路由。"""
from __future__ import annotations

import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import time
from typing import Any, AsyncGenerator, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.core import llm_client
from src.core.config import settings
from src.qa.rag import ask, ask_stream
from src.qa import conversation_context
from src.core.deep_ai_options import DeepAiOptions
from src.core.retrieval_modes import RetrievalMode, VALID_MODES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/qa", tags=["qa"])


class AskRequest(BaseModel):
    operation: Literal["ask", "expand"] = "ask"
    deep_ai_options: DeepAiOptions | None = None
    api_retry_count: int = Field(10, ge=0, le=10, strict=True)
    strict_knowledge: bool = False
    provider_id: str | None = None
    question: str = Field(..., min_length=1, max_length=4000)
    top_k: int | None = Field(None, ge=1, le=20)
    history: list[dict] | None = None
    kb_scope: str = Field("default", description="知识库 ID（默认 'default'）")
    # AI 日志关联（可选）：流式问答时前端传，便于日志页跳转到对应会话
    session_id: str | None = None
    turn_id: str | None = None
    # 检索模式：basic/deep 不调 LLM 直接返回片段；ai 走 LLM 流式作答。
    # 不传时读会话的 retrieval_mode，都没有则 ai。
    mode: RetrievalMode | None = None


class CitationModel(BaseModel):
    source_path: str
    source_name: str
    title: str
    section_label: str
    file_type: str
    text_snippet: str
    score: float


class AskResponse(BaseModel):
    question: str
    answer: str
    citations: list[CitationModel]
    used_provider: str
    used_chunks: int
    trace: list[dict[str, Any]] = Field(default_factory=list)
    mode: RetrievalMode = "ai"
    sources: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/ask", response_model=AskResponse)
def ask_endpoint(req: AskRequest) -> AskResponse:
    """问答接口。"""
    if req.operation != "ask":
        raise HTTPException(400, "扩展检索请使用流式接口")
    from src.knowledge import rebuild
    if rebuild.is_rebuilding():
        raise HTTPException(
            status_code=503,
            detail="向量库重建中，请等待重建完成后再提问",
        )
    bundle = None
    if _resolve_mode(req) in ('ai', 'deep_ai'):
        bundle = asyncio.run(conversation_context.prepare(req.question, req.history, req.session_id, req.turn_id, req.kb_scope, req.api_retry_count, background=False))
    context_token = conversation_context.active.set(bundle)
    try:
        result = ask(
            question=req.question,
            top_k=req.top_k,
            history=req.history,
            kb_scope=req.kb_scope,
            mode=_resolve_mode(req),
            strict_knowledge=req.strict_knowledge,
            api_retry_count=req.api_retry_count,
            deep_ai_options=req.deep_ai_options,
        )
    except llm_client.NoAvailableProviderError as e:
        raise HTTPException(
            status_code=503,
            detail=f"没有可用的 LLM provider，请检查 .env 中的 API key。原因: {e}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"问答失败: {e}")

    finally:
        conversation_context.active.reset(context_token)
    if bundle:
        result.trace.insert(0, bundle.stage())

    from src.core import accounts
    if accounts.enabled:
        accounts.require_kb(req.kb_scope)
        if accounts.selected_provider.get(): accounts.resolve_provider(accounts.selected_provider.get())
    return AskResponse(
        question=result.question,
        answer=result.answer,
        citations=[CitationModel(**c.to_dict()) for c in result.citations],
        used_provider=result.used_provider,
        used_chunks=result.used_chunks,
        trace=result.trace,
        mode=result.mode,
        sources=result.sources,
    )


def _resolve_mode(req: AskRequest) -> str:
    """检索模式优先级：请求体 > 会话 retrieval_mode > 'ai'。"""
    if req.mode:
        return req.mode
    if req.session_id:
        from src.db import metadata_db
        try:
            s = metadata_db.get_session(req.session_id)
            if s and s.get("retrieval_mode") in VALID_MODES:
                return "basic" if s["retrieval_mode"] == "deep" else s["retrieval_mode"]
        except Exception:
            pass
    return "ai"


# Per-turn guard also covers repeated clicks from separate browser tabs.
_active_expansions: set[tuple[str, str]] = set()


def _expansion_turn(req: AskRequest) -> dict:
    from src.db import metadata_db
    from src.core import accounts
    if not req.session_id or not req.turn_id or req.mode != "deep":
        raise HTTPException(400, "扩展检索需要指定会话、问答和 deep 检索操作")
    if accounts.enabled:
        accounts.require_owner("session", req.session_id)
        accounts.require_kb(req.kb_scope)
    session = metadata_db.get_session(req.session_id)
    turn = metadata_db.get_turn(req.session_id, req.turn_id)
    if not session or not turn:
        raise HTTPException(404, "原问答已不存在")
    if session.get("kb_scope") != req.kb_scope or turn["question"] != req.question:
        raise HTTPException(400, "扩展检索必须使用原问题和原知识库")
    if turn.get("mode") not in ("basic", "deep"):
        raise HTTPException(400, "仅检索结果支持扩展检索")
    return turn


def _save_expansion(req: AskRequest, data: dict) -> dict:
    from src.db import metadata_db
    from src.api.routes_sessions import _trim_sources, _trim_trace
    _expansion_turn(req)  # Recheck ownership, access and original turn before saving.
    result = {
        "sources": _trim_sources(data.get("sources"), long_content=True),
        "trace": _trim_trace(data.get("trace")),
        "completed_at": int(time.time() * 1000),
    }
    if not metadata_db.update_turn(req.session_id, req.turn_id,
            expansion_json=json.dumps(result, ensure_ascii=False)):
        raise HTTPException(404, "原问答已不存在，扩展结果未保存")
    return result


_access_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="qa-access")
_ACCESS_WAIT = 5.0
_ACCESS_GRACE = 10.0


async def _check_stream_access(check, req):
    """Keep authorization off the retrieval pool; never release unchecked output."""
    started = time.monotonic()
    task = asyncio.get_running_loop().run_in_executor(
        _access_executor, contextvars.copy_context().run, check)
    try:
        try:
            await asyncio.wait_for(asyncio.shield(task), _ACCESS_WAIT)
        except asyncio.TimeoutError:
            logger.warning("stream access delayed session=%s turn=%s operation=%s elapsed_ms=%.0f",
                           req.session_id, req.turn_id, req.operation, (time.monotonic()-started)*1000)
            # Continue waiting for the same check, without duplicating DB work.
            await asyncio.wait_for(asyncio.shield(task), _ACCESS_GRACE)
    finally:
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        elapsed = (time.monotonic() - started) * 1000
        if elapsed >= 1000:
            logger.info("stream access finished session=%s turn=%s elapsed_ms=%.0f", req.session_id, req.turn_id, elapsed)


async def _sse_events(req: AskRequest) -> AsyncGenerator[bytes, None]:
    """把 ask_stream 的事件流转成 SSE 格式，15s 无事件时发 ping。"""
    queue: asyncio.Queue = asyncio.Queue()
    start_time = time.time()
    mode = await asyncio.to_thread(_resolve_mode, req)
    expansion_key = (req.session_id, req.turn_id)
    if req.operation == "expand":
        if expansion_key in _active_expansions:
            yield 'event: error\ndata: {"message":"该问答正在扩展检索，请稍候"}\n\n'.encode("utf-8")
            return
        _active_expansions.add(expansion_key)
    logger.info("ask_stream start mode=%s operation=%s session=%s turn=%s",
                "basic" if req.operation == "expand" else mode, req.operation, req.session_id, req.turn_id)

    async def producer() -> None:
        from src.core.api_retry import retry_observer
        retry_stages = []
        loop = asyncio.get_running_loop()
        def on_retry(**info):
            data = {"stage": "api_retry", "label": "API 请求重试", "status": "partial",
                    "notes": f"{info['reason']}，{info['delay']:.1f} 秒后进行第 {info['attempt']}/{info['maximum']} 次重试"}
            retry_stages.append(data)
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "stage", "data": data})
        observer_token = retry_observer.set(on_retry)
        context_token = None
        bundle = None
        try:
            if mode in ('ai', 'deep_ai'):
                await queue.put({'type': 'stage', 'data': {'stage': 'context_loading', 'label': '读取会话上下文', 'status': 'ok', 'notes': '准备历史问答、摘要及个人偏好'}})
                bundle = await conversation_context.prepare(req.question, req.history, req.session_id, req.turn_id, req.kb_scope, req.api_retry_count)
                context_token = conversation_context.active.set(bundle)
                await queue.put({'type': 'stage', 'data': bundle.stage()})
            async for evt in ask_stream(
                question=req.question,
                top_k=req.top_k,
                history=req.history,
                kb_scope=req.kb_scope,
                session_id=req.session_id,
                turn_id=req.turn_id,
                mode=mode,
                strict_knowledge=req.strict_knowledge,
                api_retry_count=req.api_retry_count,
                deep_ai_options=req.deep_ai_options,
            ):
                if evt.get('type') in ('done', 'error') and bundle:
                    evt['data']['trace'] = [bundle.stage(), *evt['data'].get('trace', [])]
                if evt.get("type") in ("done", "error") and retry_stages:
                    evt["data"]["trace"] = [*retry_stages, *evt["data"].get("trace", [])]
                await queue.put(evt)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"ask_stream producer error: {e}")
            await queue.put({"type": "error", "data": {"message": str(e)}})
        finally:
            if context_token is not None:
                conversation_context.active.reset(context_token)
            retry_observer.reset(observer_token)
            await queue.put(None)

    producer_task = asyncio.create_task(producer())

    final_answer_len = 0
    final_chunks = 0
    outcome = "cancelled"
    pending = None

    def check_access():
        from src.core import accounts
        if accounts.enabled:
            accounts.require_kb(req.kb_scope)
            if accounts.selected_provider.get():
                accounts.resolve_provider(accounts.selected_provider.get())

    try:
        while True:
            try:
                evt = pending if pending is not None else await asyncio.wait_for(queue.get(), timeout=15.0)
                pending = None
            except asyncio.TimeoutError:
                yield b": ping\n\n"
                continue
            if evt is None:
                break
            # Validate once per output batch; never run SQLite on the event loop.
            if evt.get("type") == "token":
                texts = [evt["data"]["text"]]
                while not queue.empty():
                    following = queue.get_nowait()
                    if following is None:
                        queue.put_nowait(None)
                        break
                    if following.get("type") != "token":
                        pending = following
                        break
                    texts.append(following["data"]["text"])
                evt = {"type": "token", "data": {"text": "".join(texts)}}
            output_stage = evt.get("data", {}).get("stage", evt.get("type")) if isinstance(evt.get("data"), dict) else evt.get("type")
            if evt.get('type') != 'error':
                try:
                    await _check_stream_access(check_access, req)
                except HTTPException as exc:
                    evt = {'type':'error','data':{'message':exc.detail}}
                except asyncio.TimeoutError:
                    evt = {'type':'error','data':{'message':'权限检查超时，请稍后重试。'}}
            if evt.get("type") == "done" and req.operation == "expand":
                try:
                    evt["data"]["expansion"] = await asyncio.to_thread(_save_expansion, req, evt["data"])
                except Exception as exc:
                    logger.exception("expansion save failed session=%s turn=%s", req.session_id, req.turn_id)
                    evt = {"type": "error", "data": {"message": str(getattr(exc, "detail", exc))}}
            evt_type = evt.get("type", "message")
            data = evt.get("data", {})
            if evt_type == "done":
                outcome = data.get("outcome", "success")
                final_answer_len = len(data.get("answer", "")) if isinstance(data, dict) else 0
                final_chunks = data.get("used_chunks", 0) if isinstance(data, dict) else 0
            elif evt_type == "error":
                outcome = "failed"
                logger.warning("stream failed session=%s turn=%s operation=%s stage=%s reason=%s",
                               req.session_id, req.turn_id, req.operation, output_stage, str(data.get("message", ""))[:1000])
            payload = f"event: {evt_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            yield payload.encode("utf-8")
            if evt_type == "done" or evt_type == "error":
                break
    finally:
        if req.operation == "expand":
            _active_expansions.discard(expansion_key)
        duration_ms = (time.time() - start_time) * 1000
        logger.info("ask_stream finished session=%s turn=%s operation=%s outcome=%s %.0fms chunks=%s answer_len=%s",
                    req.session_id, req.turn_id, req.operation, outcome, duration_ms, final_chunks, final_answer_len)
        if not producer_task.done():
            producer_task.cancel()
            try:
                await producer_task
            except BaseException:
                pass


@router.post("/ask_stream")
async def ask_stream_endpoint(req: AskRequest):
    """流式问答 SSE 接口。返回 text/event-stream。

    事件类型：
        warmup → 冷启动提示
        stage → RAG 阶段完成
        sources → 命中 chunks 摘要
        token → LLM token
        done → 完整答案 + trace
        error → 错误信息
    """
    if req.operation == "expand":
        await asyncio.to_thread(_expansion_turn, req)
    from src.knowledge import rebuild
    if rebuild.is_rebuilding():
        raise HTTPException(
            status_code=503,
            detail="向量库重建中，请等待重建完成后再提问",
        )
    return StreamingResponse(
        _sse_events(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
        },
    )


class SummarizeTitleRequest(BaseModel):
    provider_id: str | None = None
    question: str = Field(..., min_length=1, max_length=4000)
    answer: str = Field("", max_length=8000)
    session_id: str | None = None  # 用于 AI 日志关联


class SummarizeTitleResponse(BaseModel):
    title: str


@router.post("/summarize-title", response_model=SummarizeTitleResponse)
def summarize_title_endpoint(req: SummarizeTitleRequest) -> SummarizeTitleResponse:
    """根据问答对生成 4-8 字会话标题。失败时回退到问题前 16 字。"""
    prompt = (
        "请把下面的问答对总结成一个 4-8 个字的中文短标题，要求：\n"
        "1. 概括问题主题，不要包含「问答」「对话」这类词\n"
        "2. 不要标点符号结尾\n"
        "3. 直接输出标题，不要解释\n\n"
        f"问题：{req.question}\n"
        f"回答（前 500 字）：{req.answer[:500]}\n\n"
        "标题："
    )
    try:
        client = llm_client.get_client()
        chat_cfg = settings.config.get("qa", {}).get("chat_options", {})
        title, _ = client.chat(
            [{"role": "user", "content": prompt}],
            temperature=chat_cfg.get("temperature", 0.2),
            max_tokens=30,
            scene="title",
            log_meta={"session_id": req.session_id},
        )
    except Exception:
        title = req.question[:16]

    title = title.strip().strip("\"'""''「」【】").replace("\n", " ")
    if not title:
        title = req.question[:16]
    return SummarizeTitleResponse(title=title[:30])
