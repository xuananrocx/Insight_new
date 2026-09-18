"""问答相关 API 路由。"""
from __future__ import annotations

import asyncio
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/qa", tags=["qa"])


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    top_k: int | None = Field(None, ge=1, le=20)
    history: list[dict] | None = None
    kb_scope: str = Field("default", description="知识库 ID（默认 'default'）")
    # AI 日志关联（可选）：流式问答时前端传，便于日志页跳转到对应会话
    session_id: str | None = None
    turn_id: str | None = None
    # 检索模式：basic/deep 不调 LLM 直接返回片段；ai 走 LLM 流式作答。
    # 不传时读会话的 retrieval_mode，都没有则 ai。
    mode: Literal["basic", "deep", "ai"] | None = None


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


@router.post("/ask", response_model=AskResponse)
def ask_endpoint(req: AskRequest) -> AskResponse:
    """问答接口。"""
    from src.knowledge import rebuild
    if rebuild.is_rebuilding():
        raise HTTPException(
            status_code=503,
            detail="向量库重建中，请等待重建完成后再提问",
        )
    try:
        result = ask(
            question=req.question,
            top_k=req.top_k,
            history=req.history,
            kb_scope=req.kb_scope,
        )
    except llm_client.NoAvailableProviderError as e:
        raise HTTPException(
            status_code=503,
            detail=f"没有可用的 LLM provider，请检查 .env 中的 API key。原因: {e}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"问答失败: {e}")

    return AskResponse(
        question=result.question,
        answer=result.answer,
        citations=[CitationModel(**c.to_dict()) for c in result.citations],
        used_provider=result.used_provider,
        used_chunks=result.used_chunks,
        trace=result.trace,
    )


def _resolve_mode(req: AskRequest) -> str:
    """检索模式优先级：请求体 > 会话 retrieval_mode > 'ai'。"""
    if req.mode:
        return req.mode
    if req.session_id:
        from src.db import metadata_db
        try:
            s = metadata_db.get_session(req.session_id)
            if s and s.get("retrieval_mode") in ("basic", "deep", "ai"):
                return s["retrieval_mode"]
        except Exception:
            pass
    return "ai"


async def _sse_events(req: AskRequest) -> AsyncGenerator[bytes, None]:
    """把 ask_stream 的事件流转成 SSE 格式，15s 无事件时发 ping。"""
    queue: asyncio.Queue = asyncio.Queue()
    start_time = time.time()
    q_preview = req.question[:50].replace("\n", " ")
    mode = _resolve_mode(req)
    logger.info(f"ask_stream start q={q_preview!r} mode={mode} history_len={len(req.history) if req.history else 0}")

    async def producer() -> None:
        try:
            async for evt in ask_stream(
                question=req.question,
                top_k=req.top_k,
                history=req.history,
                kb_scope=req.kb_scope,
                session_id=req.session_id,
                turn_id=req.turn_id,
                mode=mode,
            ):
                await queue.put(evt)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"ask_stream producer error: {e}")
            await queue.put({"type": "error", "data": {"message": str(e)}})
        finally:
            await queue.put(None)

    producer_task = asyncio.create_task(producer())

    final_answer_len = 0
    final_chunks = 0
    try:
        while True:
            try:
                evt = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield b": ping\n\n"
                continue
            if evt is None:
                break
            evt_type = evt.get("type", "message")
            data = evt.get("data", {})
            if evt_type == "done":
                final_answer_len = len(data.get("answer", "")) if isinstance(data, dict) else 0
                final_chunks = data.get("used_chunks", 0) if isinstance(data, dict) else 0
            payload = f"event: {evt_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            yield payload.encode("utf-8")
            if evt_type == "done" or evt_type == "error":
                break
    finally:
        duration_ms = (time.time() - start_time) * 1000
        logger.info(f"ask_stream done {duration_ms:.0f}ms chunks={final_chunks} answer_len={final_answer_len}")
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
