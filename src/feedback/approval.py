"""反馈与审批：把点赞的 Q&A 加入队列 → PM 审批 → 入库。

通道1：人工反哺（点赞触发）
通道2：日志反哺（Phase 2 用，预留接口）
通道3：自动反哺（配置项控制，默认关闭，需要审批）
"""
from __future__ import annotations

from typing import Any

from src.core.config import settings
from src.db import metadata_db
from src.qa.rag import add_approved_qa


def submit_feedback(
    question: str,
    answer: str,
    sources: list[dict],
    used_provider: str,
    rating: int,
    user_comment: str = "",
) -> int | None:
    """提交反馈。rating=1 赞，-1 踩，0 中性。

    策略：
    - 赞（rating=1）：入队，等 PM 审批
    - 踩（rating=-1）：不入队，仅记录（用于未来 negative mining）
    - 中性（rating=0）：不入队

    返回 feedback_id 或 None（未入队时）。
    """
    if rating != 1:
        # 暂不处理踩的反馈（可加统计表，Phase 2）
        return None

    # 自动反哺开关（默认关闭）
    auto_feedback_cfg = settings.config.get("feature_flags", {}).get(
        "feedback_loop", {}
    ).get("auto_feedback", {})
    auto_enabled = auto_feedback_cfg.get("enabled", False)

    from src.core import accounts
    if not accounts.enabled and auto_enabled and not auto_feedback_cfg.get("require_approval", True):
        # 直接入库（不推荐，但允许）
        add_approved_qa(question, answer)
        return None

    # 默认走审批通道
    if not settings.is_enabled("feedback_loop.manual_feedback"):
        return None

    return metadata_db.enqueue_feedback(
        question=question,
        answer=answer,
        sources=sources,
        used_provider=used_provider,
        rating=rating,
        user_comment=user_comment,
    )


def list_pending(limit: int = 100) -> list[dict]:
    """列出待审批的反馈。"""
    return metadata_db.list_feedback(status="pending", limit=limit)


def list_approved(limit: int = 100) -> list[dict]:
    """列出已审批通过的反馈。"""
    return metadata_db.list_feedback(status="approved", limit=limit)


def approve(
    feedback_id: int,
    reviewer: str = "pm",
    note: str = "",
) -> dict[str, Any]:
    """审批通过：把 Q&A 写入向量库 + 标记 approved。"""
    fb = metadata_db.get_feedback(feedback_id)
    if not fb:
        return {"ok": False, "error": "反馈不存在"}
    if fb["status"] != "pending":
        return {"ok": False, "error": f"反馈当前状态为 {fb['status']}，无法审批"}

    # 写入向量库（feedback collection）
    try:
        add_approved_qa(
            question=fb["question"],
            answer=fb["answer"],
            metadata={
                "feedback_id": fb["id"],
                "reviewer": reviewer,
                "original_provider": fb.get("used_provider", ""),
            },
        )
    except Exception as e:
        return {"ok": False, "error": f"写入向量库失败: {e}"}

    # 更新 db
    metadata_db.review_feedback(
        fid=feedback_id,
        decision="approved",
        reviewer=reviewer,
        note=note,
    )
    return {"ok": True, "feedback_id": feedback_id}


def reject(
    feedback_id: int,
    reviewer: str = "pm",
    note: str = "",
) -> dict[str, Any]:
    """审批拒绝：标记 rejected，不写入向量库。"""
    fb = metadata_db.get_feedback(feedback_id)
    if not fb:
        return {"ok": False, "error": "反馈不存在"}
    if fb["status"] != "pending":
        return {"ok": False, "error": f"反馈当前状态为 {fb['status']}，无法审批"}
    metadata_db.review_feedback(
        fid=feedback_id,
        decision="rejected",
        reviewer=reviewer,
        note=note,
    )
    return {"ok": True, "feedback_id": feedback_id}
