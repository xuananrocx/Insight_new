"""AI 调用日志 API 路由。"""
from __future__ import annotations

import logging
import json
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.core.config import settings
from src.core import permissions
from src.db import metadata_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ai_logs", tags=["ai_logs"])


def safe_error_summary(message: str | None) -> str | None:
    """Never forward untrusted error bodies to list-only viewers."""
    if not message:
        return None
    parts = ["调用失败"]
    try:
        data = json.loads(message)
    except (ValueError, TypeError):
        return parts[0]
    if not isinstance(data, dict):
        return parts[0]
    status = data.get("http_status")
    if type(status) is int and 400 <= status <= 599:
        parts.append(f"HTTP {status}")
    call_id = data.get("call_id")
    if isinstance(call_id, str) and re.fullmatch(r"[a-fA-F0-9]{16,32}", call_id):
        parts.append(f"调用编号：{call_id}")
    return " · ".join(parts)


class BatchDeleteRequest(BaseModel):
    """批量删除请求。"""
    before_ts: int | None = None  # 删除 created_at < before_ts 的
    provider: str | None = None
    scene: str | None = None


@router.get("", response_model=dict)
def list_logs(
    provider: str | None = None,
    scene: str | None = None,
    success: bool | None = None,
    session_id: str | None = None,
    kb_id: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    """列出 AI 调用日志（不含 messages/response 大字段）。

    用过滤参数缩小范围，详情用 GET /{id}。
    """
    metadata_db.init_db()
    items, total = metadata_db.list_ai_call_logs(
        provider=provider,
        scene=scene,
        success=success,
        session_id=session_id,
        kb_id=kb_id,
        start_ts=start_ts,
        end_ts=end_ts,
        limit=limit,
        offset=offset,
    )
    if not permissions.has("ai_logs.detail"):
        items = [{**item, "error_message": safe_error_summary(item.get("error_message"))} for item in items]
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/stats", response_model=dict)
def get_stats(
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> dict:
    """统计：按 provider / scene / day 聚合。"""
    metadata_db.init_db()
    return metadata_db.get_ai_call_log_stats(start_ts=start_ts, end_ts=end_ts)


@router.get("/config", response_model=dict)
def get_config() -> dict:
    """返回当前 AI 日志配置（前端设置页用）。

    注意：必须在 /{log_id} 之前声明，否则 'config' 会被当 log_id 参数。
    """
    cfg = settings.config.get("feature_flags", {}).get("ai_call_log", {})
    return {
        "enabled": cfg.get("enabled", True),
        "log_response": cfg.get("log_response", True),
        "log_system_prompt": cfg.get("log_system_prompt", True),
        "log_messages": cfg.get("log_messages", True),
        "retention_days": cfg.get("retention_days", 30),
        "scenes": {"conversation_summary": True, "conversation_history": True, "deep_ai_verify": True, **cfg.get("scenes", {
            "qa_chat": True,
            "summarize": True,
            "concept_extract": True,
            "title": True,
            "test": False,
        })},
    }


@router.get("/{log_id}", response_model=dict)
def get_log(log_id: int) -> dict:
    """返回单条日志详情（含完整 messages / response）。"""
    metadata_db.init_db()
    log = metadata_db.get_ai_call_log(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="日志不存在")
    return log


@router.delete("/{log_id}", response_model=dict)
def delete_log(log_id: int) -> dict:
    """删除单条日志。"""
    metadata_db.init_db()
    ok = metadata_db.delete_ai_call_log(log_id)
    if not ok:
        raise HTTPException(status_code=404, detail="日志不存在")
    return {"ok": True}


@router.post("/batch_delete", response_model=dict)
def batch_delete(req: BatchDeleteRequest) -> dict:
    """批量删除日志。

    before_ts: 删除创建时间早于该时间戳的（毫秒）
    provider: 删除指定 provider 的
    scene: 删除指定 scene 的
    """
    metadata_db.init_db()
    if not any([req.before_ts, req.provider, req.scene]):
        raise HTTPException(
            status_code=400,
            detail="必须至少传 before_ts / provider / scene 之一（避免误删全部）",
        )
    deleted = metadata_db.delete_ai_call_logs_batch(
        before_ts=req.before_ts,
        provider=req.provider,
        scene=req.scene,
    )
    logger.info(f"batch_delete ai_call_logs: deleted={deleted} req={req}")
    return {"ok": True, "deleted": deleted}


@router.post("/cleanup", response_model=dict)
def cleanup() -> dict:
    """按 retention_days 配置清理过期日志。"""
    metadata_db.init_db()
    retention = settings.config.get("feature_flags", {}).get("ai_call_log", {}).get("retention_days", 30)
    import time
    deleted = metadata_db.delete_ai_call_logs_batch(before_ts=int((time.time() - retention * 86400) * 1000))
    logger.info(f"cleanup ai_call_logs: retention={retention}d deleted={deleted}")
    return {"ok": True, "deleted": deleted, "retention_days": retention}


@router.post("/delete_all", response_model=dict)
def delete_all() -> dict:
    """删除全部 AI 调用日志（危险操作，前端必须二次确认）。

    与 batch_delete 区分：batch_delete 强制要求过滤条件，delete_all 显式删全部。
    """
    metadata_db.init_db()
    deleted = metadata_db.delete_ai_call_logs_batch()
    logger.warning(f"delete_all ai_call_logs: deleted={deleted}")
    return {"ok": True, "deleted": deleted}


class UpdateConfigRequest(BaseModel):
    enabled: bool | None = None
    log_response: bool | None = None
    log_system_prompt: bool | None = None
    log_messages: bool | None = None
    retention_days: int | None = None
    scenes: dict[str, bool] | None = None


@router.post("/config", response_model=dict)
def update_config(req: UpdateConfigRequest) -> dict:
    """更新 AI 日志配置（写回 config.yaml）。"""
    import yaml
    from pathlib import Path

    user_config_path = Path(
        settings.config.get("_user_config_path")
        if hasattr(settings, "config")
        else None
    )
    # 走标准入口：直接修改 config 对象然后写回
    # 简化方案：直接读 + 改 + 写
    config_path = settings.config.get("_config_path")
    if not config_path:
        # 兜底：从 settings 反推
        config_path = str(Path.home() / "Library" / "Application Support" / "AI-Assistant" / "config.yaml")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"config 文件不存在: {config_path}")

    feature_flags = cfg.setdefault("feature_flags", {})
    ai_log_cfg = feature_flags.setdefault("ai_call_log", {})

    if req.enabled is not None:
        ai_log_cfg["enabled"] = req.enabled
    if req.log_response is not None:
        ai_log_cfg["log_response"] = req.log_response
    if req.log_system_prompt is not None:
        ai_log_cfg["log_system_prompt"] = req.log_system_prompt
    if req.log_messages is not None:
        ai_log_cfg["log_messages"] = req.log_messages
    if req.retention_days is not None:
        ai_log_cfg["retention_days"] = max(1, min(365, req.retention_days))
    if req.scenes is not None:
        existing_scenes = ai_log_cfg.get("scenes", {})
        existing_scenes.update(req.scenes)
        ai_log_cfg["scenes"] = existing_scenes

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"写 config 失败: {e}")

    # 同步到内存（settings 单例的 _config）
    settings.config["feature_flags"]["ai_call_log"] = ai_log_cfg

    logger.info(f"ai_call_log config updated: {ai_log_cfg}")
    return {"ok": True, "config": ai_log_cfg}
