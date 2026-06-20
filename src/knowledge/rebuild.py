"""向量库重建编排。

场景：用户切换 embedding 模型导致维度变化时，需要：
1. 切换 embedder
2. 清空主库 + feedback 库 + BM25 索引
3. 重新 ingest 所有源文档 + 已审批 feedback
4. 期间禁止 Q&A（routes_qa.py 检查 is_rebuilding()）

失败回滚策略：
- 源文档和 metadata_db 是真源，向量是派生数据
- 重建失败 → 切回旧模型 + 用旧模型重新 ingest → 恢复到原始状态
- 不需要单独的向量备份文件

状态机：idle → in_progress → succeeded | failed → idle
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from src.core import llm_client, vector_store
from src.core.config import settings
from src.db import metadata_db
from src.knowledge import ingestion
from src.qa import bm25_index
from src.qa.rag import add_approved_qa

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_state: dict[str, Any] = {
    "status": "idle",
    "model_name": None,
    "started_at": None,
    "completed_at": None,
    "failed_at": None,
    "current": 0,
    "total": 0,
    "current_file": "",
    "stage": "",
    "error": None,
}

# 进度状态锁：保护 _state 的多字段原子更新（rebuild 线程写，HTTP 轮询线程读）
_state_lock = threading.Lock()

# 节流：progress_cb 高频调用时只在间隔 >= _PROGRESS_THROTTLE_S 时才更新 stage 文本
# current/current_file 计数仍每次更新（不会高频，每个文件 1 次）
_PROGRESS_THROTTLE_S = 0.2
_last_stage_update_ts: float = 0.0

# 粗估：单文档平均耗时（秒）—— bge-base CPU 经验值
_SECS_PER_DOC = 4.0


def _set_state(**kwargs: Any) -> None:
    """线程安全更新 _state（rebuild 线程写，HTTP 轮询线程读）。"""
    with _state_lock:
        _state.update(kwargs)


def _incr_current() -> None:
    """线程安全 _state['current'] += 1。"""
    with _state_lock:
        _state["current"] = _state.get("current", 0) + 1


def get_status() -> dict[str, Any]:
    """读 _state 快照（线程安全）。"""
    with _state_lock:
        return dict(_state)


def recover_state_on_startup() -> None:
    """启动时检查 app_state 是否记录了未完成的 rebuild。

    如果进程在 rebuild 期间被 kill / OOM，内存 _state 会重置为 idle，
    但向量库已被 reset_collection 清空且未重新 ingest——数据丢失。

    修复：rebuild 开始时把状态写入 app_state；启动时检查并提示用户。
    实际数据无法自动恢复（旧 chunks 已删），但能让用户立即感知。
    """
    try:
        from src.db import metadata_db
        marker = metadata_db.get_state("rebuild_in_progress")
        if not marker:
            return
        # marker 存在 → 上次 rebuild 未正常完成
        # 清理 marker（提示一次即可）
        metadata_db.delete_state("rebuild_in_progress")
        # 检查向量库是否真的为空
        chunks_count = vector_store.count_chunks()
        if chunks_count == 0:
            # 数据已丢，更新内存 _state 让前端感知（前端轮询 rebuild/status 看到 failed）
            logger.error(
                "[rebuild] 检测到上次 rebuild 未完成，向量库已清空！"
                "数据丢失，需要重新投喂文档或重新触发 rebuild。"
            )
            _set_state(
                status="failed",
                failed_at=time.time(),
                stage="上次 rebuild 被中断，向量库已清空",
                error="进程在 rebuild 中途被中断，向量库已清空，需重新投喂或 rebuild",
            )
        else:
            # chunks 不为空：可能 rebuild 已完成大部分但 marker 未清，标记成功即可
            logger.info(
                f"[rebuild] 检测到上次 rebuild marker 但向量库仍有 {chunks_count} chunks，"
                f"视为已完成"
            )
    except Exception as e:
        logger.warning(f"[rebuild] recover_state_on_startup 失败（非致命）: {e}")


def _mark_rebuild_started() -> None:
    """rebuild 开始时写入 app_state（用于崩溃恢复检测）。"""
    try:
        from src.db import metadata_db
        metadata_db.set_state("rebuild_in_progress", "1")
    except Exception as e:
        logger.warning(f"[rebuild] 标记 rebuild_in_progress 失败: {e}")


def _mark_rebuild_finished() -> None:
    """rebuild 完成时清除 app_state 标记。"""
    try:
        from src.db import metadata_db
        metadata_db.delete_state("rebuild_in_progress")
    except Exception as e:
        logger.warning(f"[rebuild] 清除 rebuild_in_progress 标记失败: {e}")


def is_rebuilding() -> bool:
    return _state["status"] == "in_progress"


def precheck(new_model: str) -> dict[str, Any]:
    """切换前预检：是否需要重建、文档数、预估耗时。"""
    status = llm_client.get_client().get_embedding_status()
    available = status.get("available_models", [])
    new_dim = next((m["dimensions"] for m in available if m["name"] == new_model), None)
    current_dim = vector_store.get_collection_dim(vector_store.COLLECTION_NAME)

    metadata_db.init_db()
    all_files = metadata_db.list_files(limit=100000)
    doc_count = sum(1 for f in all_files if f.get("status") == "done")

    needs_rebuild = (
        new_dim is not None
        and current_dim > 0
        and new_dim != current_dim
    )

    return {
        "needs_rebuild": needs_rebuild,
        "current_dim": current_dim,
        "new_dim": new_dim or 0,
        "doc_count": doc_count,
        "est_seconds": int(doc_count * _SECS_PER_DOC) if needs_rebuild else 0,
    }


def rebuild_and_switch(new_model: str) -> None:
    """后台执行：切换 + 重建。失败自动回滚。

    调用方应在后台线程里跑（避免阻塞 HTTP 请求）。
    """
    with _lock:
        if _state["status"] == "in_progress":
            raise RuntimeError("已有重建任务进行中")
        # P1-17: marker 在 _set_state 之前写，确保崩溃恢复能检测到
        # 中间窗口（marker 写了但 _state 还是 idle）下下次启动 recover 会清 marker
        _mark_rebuild_started()
        _set_state(
            status="in_progress",
            model_name=new_model,
            started_at=time.time(),
            completed_at=None,
            failed_at=None,
            current=0,
            total=0,
            current_file="",
            stage="切换 embedding 模型",
            error=None,
        )

    old_model = llm_client.get_client().get_embedding_status().get("current_model")
    logger.info(f"[rebuild] 开始：{old_model} → {new_model}")

    try:
        _do_rebuild(new_model)
        _set_state(
            status="succeeded",
            completed_at=time.time(),
            stage="完成",
            current_file="",
        )
        _mark_rebuild_finished()
        logger.info(f"[rebuild] 成功完成")
    except Exception as e:
        logger.exception(f"[rebuild] 失败，开始回滚到 {old_model}")
        _set_state(stage=f"失败：{e}", error=str(e))
        try:
            if old_model and old_model != new_model:
                _do_rebuild(old_model)
        except Exception as rollback_err:
            logger.exception(f"[rebuild] 回滚也失败：{rollback_err}")
            _set_state(error=f"重建失败：{e}；回滚也失败：{rollback_err}")
        else:
            _set_state(error=f"重建失败，已回滚到 {old_model}：{e}")
        _set_state(
            status="failed",
            failed_at=time.time(),
        )
        _mark_rebuild_finished()  # 失败也清除 marker（避免下次启动误报）


def _do_rebuild(model_name: str) -> None:
    """实际重建工作。任何步骤抛异常都触发上层回滚。"""
    client = llm_client.get_client()

    # 1. 切换 embedding 模型
    _set_state(stage=f"加载模型 {model_name}")
    client.switch_embedding_model(model_name)

    # 2. 统计待处理文档数
    metadata_db.init_db()
    all_files = metadata_db.list_files(limit=100000)
    done_files = [f for f in all_files if f.get("status") == "done"]
    _set_state(total=len(done_files), current=0)

    # 3. 重置向量库 collection + BM25
    _set_state(stage="清空旧向量库")
    vector_store.reset_collection(vector_store.COLLECTION_NAME)
    vector_store.reset_collection(vector_store.FEEDBACK_COLLECTION_NAME)
    bm25_index.clear()

    # 4. 重新 ingest（force=True 跳过 hash 检查）
    _set_state(stage="重新 embedding 文档")

    def progress_cb(stage: str, info: dict) -> None:
        """ingestion 进度回调。

        parsing 阶段每个文件只触发一次（计数 +1），其他阶段（chunking/embedding/upserting）
        可能对单个文件多次触发——这些非计数更新走节流，避免高频 SQLite/log。
        """
        global _last_stage_update_ts
        fname = info.get("file", "")
        if stage == "scanning":
            _set_state(stage="扫描文件")
        elif stage == "extracting":
            _set_state(stage=f"解压 {info.get('package', '')}")
        elif stage == "parsing":
            # 每个文件第一次进入 parsing 时计数 +1
            _incr_current()
            _set_state(stage=f"处理 {fname}", current_file=fname)
        elif stage in ("chunking", "embedding", "upserting"):
            # 节流：同文件的 chunking/embedding/upserting 高频触发，限制 200ms 一次
            now = time.monotonic()
            if now - _last_stage_update_ts >= _PROGRESS_THROTTLE_S:
                _set_state(stage=f"{stage} {fname}", current_file=fname)
                _last_stage_update_ts = now

    ingestion.scan_feed_folder(progress=progress_cb, force=True)

    # 4.5. 同步所有 KB 的 embedding_model/dim 字段（rebuild 后所有 KB 都用新模型）
    try:
        emb_status = client.get_embedding_status()
        new_model = emb_status.get("current_model")
        new_dim = emb_status.get("dimensions")
        if new_model and new_dim:
            for kb in metadata_db.list_kbs(limit=1000):
                if kb.get("embedding_model") != new_model or kb.get("embedding_dim") != new_dim:
                    metadata_db.update_kb(
                        kb["id"],
                        embedding_model=new_model,
                        embedding_dim=new_dim,
                    )
            logger.info(f"[rebuild] 已更新所有 KB 的 embedding 字段为 {new_model} ({new_dim}维)")
    except Exception as e:
        logger.warning(f"[rebuild] 更新 KB embedding 字段失败（非致命）：{e}")

    # 5. 重新 embedding 已审批的 feedback Q&A
    _set_state(stage="恢复已审批 Q&A")
    try:
        with metadata_db.get_cursor() as cur:
            cur.execute(
                "SELECT question, answer, reviewer, id, used_provider FROM feedback_queue WHERE status = 'approved'"
            )
            rows = [dict(r) for r in cur.fetchall()]
        for row in rows:
            add_approved_qa(
                question=row["question"],
                answer=row["answer"],
                metadata={
                    "feedback_id": row["id"],
                    "reviewer": row["reviewer"] or "",
                    "original_provider": row["used_provider"] or "",
                },
            )
        if rows:
            logger.info(f"[rebuild] 恢复 {len(rows)} 条已审批 Q&A")
    except Exception as e:
        logger.warning(f"[rebuild] 恢复 feedback 失败（非致命）：{e}")
