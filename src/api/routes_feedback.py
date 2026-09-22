"""反馈与审批 API 路由。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.core.config import settings
from src.db import metadata_db
from src.feedback import approval

router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])


class FeedbackRequest(BaseModel):
    question: str
    answer: str
    sources: list[dict] = Field(default_factory=list)
    used_provider: str = ""
    rating: int = Field(..., ge=-1, le=1)   # 1=赞 -1=踩 0=中性
    user_comment: str = ""


class FeedbackResponse(BaseModel):
    ok: bool
    feedback_id: int | None = None
    message: str = ""


@router.post("", response_model=FeedbackResponse)
def submit_feedback(req: FeedbackRequest) -> FeedbackResponse:
    """提交反馈（点赞/点踩）。"""
    if not settings.is_enabled("qa.feedback_buttons"):
        return FeedbackResponse(ok=False, message="反馈功能未启用")
    fid = approval.submit_feedback(
        question=req.question,
        answer=req.answer,
        sources=req.sources,
        used_provider=req.used_provider,
        rating=req.rating,
        user_comment=req.user_comment,
    )
    if fid is None:
        return FeedbackResponse(
            ok=True,
            message="反馈已记录但未入队（可能 rating<=0 或功能未启用）",
        )
    return FeedbackResponse(ok=True, feedback_id=fid, message="已加入待审批队列")


@router.get("/pending", response_model=dict)
def list_pending(limit: int = 100) -> dict:
    """列出待审批反馈。"""
    items = approval.list_pending(limit=limit)
    return {"items": items, "count": len(items)}


@router.get("/approved", response_model=dict)
def list_approved(limit: int = 100) -> dict:
    """列出已通过反馈。"""
    items = approval.list_approved(limit=limit)
    return {"items": items, "count": len(items)}


class ReviewRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    reviewer: str = "pm"
    note: str = ""


@router.post("/{feedback_id}/review", response_model=dict)
def review(feedback_id: int, req: ReviewRequest) -> dict:
    """审批反馈。"""
    from src.core import accounts
    if accounts.enabled:
        metadata_db.review_feedback(feedback_id,req.decision,reviewer=accounts.user()['username'],note=req.note)
        return {"ok":True,"feedback_id":feedback_id}
    if req.decision == "approved":
        result = approval.approve(feedback_id, reviewer=req.reviewer, note=req.note)
    else:
        result = approval.reject(feedback_id, reviewer=req.reviewer, note=req.note)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "审批失败"))
    return result
