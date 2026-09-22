"""AI 调用日志记录器。

设计：
- 独立模块，被 LLMClient 调用
- 读取 feature_flags.ai_call_log 配置决定是否记录
- 异常安全：日志失败不影响主流程
- 性能：异步写库（用线程池），不阻塞 LLM 调用

scene 取值：
- qa_chat: 主问答
- summarize: 文档 AI 摘要
- concept_extract: 概念提取
- title: 会话标题总结
- test: provider 测试 / system prompt 测试
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from src.core.config import settings
from src.db import metadata_db

logger = logging.getLogger(__name__)

# 后台写线程（避免阻塞 LLM 调用）
_log_queue: list[dict[str, Any]] = []
_queue_lock = threading.Lock()
_writer_thread: threading.Thread | None = None


def _ensure_writer() -> None:
    """启动后台写线程（懒加载）。"""
    global _writer_thread
    if _writer_thread is not None and _writer_thread.is_alive():
        return
    _writer_thread = threading.Thread(
        target=_writer_loop, daemon=True, name="ai-call-log-writer"
    )
    _writer_thread.start()


def _writer_loop() -> None:
    """后台循环：批量从队列取日志写库。"""
    while True:
        try:
            with _queue_lock:
                batch = list(_log_queue)
                _log_queue.clear()
            if not batch:
                # 空队列，等待下次被唤醒
                event = threading.Event()
                event.wait(timeout=2.0)
                continue
            # 批量写库
            for log_data in batch:
                try:
                    metadata_db.init_db()
                    metadata_db.insert_ai_call_log(**log_data)
                except Exception as e:
                    logger.warning(f"insert ai_call_log failed: {e}")
        except Exception as e:
            logger.exception(f"ai_call_log writer crashed: {e}")


def _is_scene_enabled(scene: str) -> bool:
    """检查 scene 是否启用记录。"""
    if not settings.is_enabled("ai_call_log.enabled", default=True):
        return False
    # scenes 子配置
    return settings.is_enabled(f"ai_call_log.scenes.{scene}", default=True)


def _should_log_field(field: str) -> bool:
    """检查是否记录某字段（response / system_prompt / messages）。"""
    return settings.is_enabled(f"ai_call_log.log_{field}", default=True)


def log_call(
    *,
    provider: str,
    model: str | None,
    scene: str,
    messages: list[dict],
    response_text: str | None = None,
    system_prompt: str | None = None,
    duration_ms: int | None = None,
    success: bool = True,
    error_message: str | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
    kb_id: str | None = None,
) -> None:
    """记录一次 AI 调用（异步，不阻塞）。

    根据 config 决定是否记录，以及记录哪些字段。
    任何异常都被吞掉（日志失败不能影响主流程）。
    """
    try:
        if not _is_scene_enabled(scene):
            logger.debug(f"ai_call_log scene={scene} disabled, skip")
            return

        from src.core import accounts
        frozen = accounts.provider_snapshot.get()
        if error_message and frozen:
            secret = accounts.provider_config(frozen)['api_key']
            if secret:
                error_message = error_message.replace(secret, '[redacted]')

        # 根据配置过滤字段
        log_data: dict[str, Any] = {
            "provider": provider,
            "model": model,
            "scene": scene,
            "messages": messages if _should_log_field("messages") else [],
            "session_id": session_id,
            "turn_id": turn_id,
            "kb_id": kb_id,
            "duration_ms": duration_ms,
            "success": success,
            "error_message": error_message,
        }
        if _should_log_field("response"):
            log_data["response_text"] = response_text
        if _should_log_field("system_prompt"):
            log_data["system_prompt"] = system_prompt

        # 入队
        from src.core import accounts
        current = accounts.identity.get()
        log_data["owner_id"] = current["id"] if current else None
        log_data["provider_id"] = accounts.selected_provider.get() or None
        with _queue_lock:
            _log_queue.append(log_data)
            # 队列过长保护（避免极端情况下内存爆炸）
            if len(_log_queue) > 500:
                _log_queue.pop(0)  # 丢弃最老的

        _ensure_writer()
        logger.debug(f"ai_call_log queued: scene={scene} provider={provider}")
    except Exception as e:
        logger.warning(f"log_call failed (suppressed): {e}")


def flush() -> None:
    """同步等待队列写完（测试用）。"""
    while True:
        with _queue_lock:
            if not _log_queue:
                return
        import time
        time.sleep(0.05)
