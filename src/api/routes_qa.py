"""问答相关 API 路由。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.core import llm_client
from src.qa.rag import ask

router = APIRouter(prefix="/api/v1/qa", tags=["qa"])


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    top_k: int | None = Field(None, ge=1, le=20)
    history: list[dict] | None = None


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


@router.post("/ask", response_model=AskResponse)
def ask_endpoint(req: AskRequest) -> AskResponse:
    """问答接口。"""
    try:
        result = ask(
            question=req.question,
            top_k=req.top_k,
            history=req.history,
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
    )
