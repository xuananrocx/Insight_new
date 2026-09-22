"""批量上传任务 worker。

设计：
- 串行处理（concurrency=1）：避免 embedding 模型锁竞争、Chroma 写入冲突
- 进度持久化到 SQLite：刷新页面 / 后端重启都能恢复
- SSE 事件流：通过 in-process pub/sub 推送，由路由层转 SSE
- 启动时自动检测"running 但实际死了"的任务 → paused
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.core.config import settings
from src.db import metadata_db
from src.knowledge import ingestion
from src.knowledge.ingestion import TaskCancelledError
from src.core.ingest_state import CleanupError, current_upload_task, ingestion_lock
from src.knowledge.ingest_transaction import cleanup_run, pending_runs

logger = logging.getLogger(__name__)

# 全局单线程 worker，避免并发 ingest
_worker_lock = threading.Lock()
_registry_lock = threading.RLock()
_active_workers: set[str] = set()

# in-process 事件总线：task_id → list[Callable[[dict], None]]
# worker 处理每个文件时，往所有订阅者推送事件
_subscribers: dict[str, list[Callable[[dict], None]]] = {}
_subscribers_lock = threading.Lock()


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _publish(task_id: str, event: dict[str, Any]) -> None:
    """向 task 的所有订阅者推送事件。"""
    with _subscribers_lock:
        subs = list(_subscribers.get(task_id, []))
    for cb in subs:
        try:
            cb(event)
        except Exception as e:
            logger.warning(f"subscriber callback failed: {e}")


def subscribe(task_id: str, callback: Callable[[dict], None]) -> Callable[[], None]:
    """订阅任务事件，返回 unsubscribe 函数。"""
    with _subscribers_lock:
        _subscribers.setdefault(task_id, []).append(callback)

    def _unsubscribe() -> None:
        with _subscribers_lock:
            if task_id in _subscribers:
                try:
                    _subscribers[task_id].remove(callback)
                except ValueError:
                    pass
                if not _subscribers[task_id]:
                    _subscribers.pop(task_id, None)

    return _unsubscribe


def _process_task(task_id: str) -> None:
    """串行处理一个任务的所有 queued 文件。

    处理流程：
        1. 拉取 queued 文件列表
        2. 逐个 ingest（串行，避免 embedding 锁）
        3. 每个文件完成 → 更新 upload_task_files + upload_tasks 进度
        4. 推送事件给订阅者
        5. 全部完成 → 任务 status 改 completed
    """
    task = metadata_db.get_upload_task(task_id)
    if not task:
        logger.warning(f"task {task_id} not found")
        return

    kb_id = task["kb_id"]
    skip_mode = task["skip_mode"]
    auto_ingest = bool(task["auto_ingest"])
    skip_if_exists = skip_mode == "skip"

    logger.info(
        f"batch_upload task {task_id} starting: kb={kb_id} skip={skip_mode} "
        f"auto_ingest={auto_ingest}"
    )

    _publish(task_id, {
        "type": "task_started",
        "task_id": task_id,
        "total": task["total"],
        "done": task["done"],
        "skipped": task["skipped"],
        "failed": task["failed"],
    })

    while True:
        task = metadata_db.get_upload_task(task_id)
        if task and task["status"] in ("cancelling", "cleaning"):
            _finish_cancel(task_id)
            return
        from src.core import accounts
        if accounts.enabled:
            accounts.require_kb(kb_id, "editor")
        # 每次循环重新拉 queued（支持 retry 时把 failed 重置为 queued）
        queued_files = metadata_db.list_upload_task_files(task_id, status="queued")
        if not queued_files:
            _settle_task(task_id)
            return

        # 检查任务是否被 cancel（status 改成 cancelled）
        task = metadata_db.get_upload_task(task_id)
        if not task or task["status"] in ("cancelled", "paused"):
            logger.info(f"task {task_id} cancelled")
            _publish(task_id, {"type": "task_cancelled", "task_id": task_id})
            return

        file_row = queued_files[0]
        file_id = file_row["id"]
        rel_path = file_row["relative_path"]
        abs_path_str = file_row["absolute_path"]
        abs_path = Path(abs_path_str)

        # 更新当前处理文件
        metadata_db.update_upload_task_progress(
            task_id,
            current_file_path=rel_path,
            current_stage="processing",
            current_percent=None, current_detail="准备处理",
        )
        metadata_db.update_upload_task_file_status(
            file_id, "processing", started_at=_now_ms()
        )
        _publish(task_id, {
            "type": "file_started",
            "task_id": task_id,
            "file_id": file_id,
            "relative_path": rel_path,
        })

        # 处理单文件
        error_msg: str | None = None
        skip_reason: str | None = None
        final_status: str = "done"
        if not auto_ingest:
            # 用户设置：只放到投喂目录，不 ingest
            # 此时文件已写入（upload_batch 端点负责），直接标记 done
            pass
        else:
            # Persist before publishing; throttle row updates to avoid SQLite churn.
            last_progress = [None, 0.0]
            def _on_progress(stage: str, payload: dict) -> None:
                now = time.monotonic()
                t = metadata_db.get_upload_task(task_id)
                if stage == "cleaning":
                    metadata_db.update_upload_task_progress(task_id, status="cleaning", current_stage="cleaning",
                        current_percent=None, current_detail="后台已停止处理，正在清理本次数据")
                    _publish(task_id, {"type": "task_cleaning", "task_id": task_id})
                    return
                if not t or t["status"] in ("cancelling", "cleaning", "cancelled"):
                    raise TaskCancelledError()
                final = payload.get("total") and payload.get("completed") == payload["total"]
                signature = (stage, payload.get("operation"), payload.get("completed"), payload.get("total"))
                if signature == last_progress[0] and now - last_progress[1] < 0.5 and not final:
                    return
                last_progress[:] = [signature, now]
                total, completed = payload.get("total"), payload.get("completed")
                percent = min(100, max(0, round(completed / total * 100, 1))) if total and completed is not None else None
                detail = payload.get("detail")
                if stage == "ai_summary":
                    detail = "文档已入库，正在生成 AI 摘要"
                metadata_db.update_upload_task_progress(
                    task_id, current_stage=stage, current_percent=percent, current_detail=detail)
                _publish(task_id, {
                    "type": "file_progress", "task_id": task_id,
                    "file_id": file_id, "relative_path": rel_path,
                    "stage": stage, "percent": percent, "detail": detail})

            try:
                if not abs_path.exists():
                    raise FileNotFoundError(f"文件不存在: {abs_path}")
                token = current_upload_task.set(task_id)
                try:
                    result = ingestion.ingest_single_file(
                        abs_path, kb_id=kb_id, skip_if_exists=skip_if_exists, progress=_on_progress)
                finally:
                    current_upload_task.reset(token)
                if result.get("skipped"):
                    final_status = "skipped"
                    skip_reason = result.get("skip_reason", "exists")
                elif not result.get("ok"):
                    final_status = "failed"
                    raw_errors = result.get("errors", [])
                    # errors 是 list[dict]，形如 [{"file": ..., "error": ...}]
                    if raw_errors and isinstance(raw_errors[0], dict):
                        error_msg = "; ".join(
                            str(e.get("error", e)) for e in raw_errors
                        ) or "ingest 失败"
                    else:
                        error_msg = "; ".join(str(e) for e in raw_errors) or "ingest 失败"
            except TaskCancelledError as exc:
                metadata_db.update_upload_task_file_status(
                    file_id, "done" if getattr(exc, "committed", False) else "cancelled", finished_at=_now_ms())
                _finish_cancel(task_id)
                return
            except CleanupError as exc:
                metadata_db.update_upload_task_file_status(file_id, "cleanup_failed", error_message=str(exc))
                _cleanup_failed(task_id, exc)
                return
            except Exception as e:
                logger.exception(f"ingest failed for {rel_path}")
                final_status = "failed"
                error_msg = str(e)

        # 更新文件状态
        metadata_db.update_upload_task_file_status(
            file_id,
            final_status,
            skip_reason=skip_reason if skip_reason else None,
            error_message=error_msg if error_msg else None,
            finished_at=_now_ms(),
        )

        # 更新任务级进度
        task = metadata_db.get_upload_task(task_id)
        rows = metadata_db.list_upload_task_files(task_id)
        new_done = sum(f["status"] == "done" for f in rows)
        new_skipped = sum(f["status"] == "skipped" for f in rows)
        new_failed = sum(f["status"] == "failed" for f in rows)

        # 检查是否所有文件都处理完了
        all_files = metadata_db.list_upload_task_files(task_id)
        queued_remaining = sum(1 for f in all_files if f["status"] == "queued")
        processing_remaining = sum(1 for f in all_files if f["status"] == "processing")
        if task["status"] in ("cancelling", "cleaning"):
            _finish_cancel(task_id)
            return
        is_finished = queued_remaining == 0 and processing_remaining == 0 and bool(task.get("upload_complete", 1)) and task["status"] != "cancelled"

        if is_finished:
            metadata_db.update_upload_task_progress(
                task_id,
                done=new_done,
                skipped=new_skipped,
                failed=new_failed,
                status="completed",
                current_file_path=None,
                current_stage=None,
                finished_at=_now_ms(),
            )
        else:
            metadata_db.update_upload_task_progress(
                task_id,
                done=new_done,
                skipped=new_skipped,
                failed=new_failed,
                current_file_path=None,
                current_stage=None,
            )

        _publish(task_id, {
            "type": "file_finished",
            "task_id": task_id,
            "file_id": file_id,
            "relative_path": rel_path,
            "status": final_status,
            "skip_reason": skip_reason,
            "error": error_msg,
            "done": new_done,
            "skipped": new_skipped,
            "failed": new_failed,
            "total": task["total"],
            "task_finished": is_finished,
        })

        if is_finished:
            _publish(task_id, {
                "type": "task_completed",
                "task_id": task_id,
                "total": task["total"],
                "done": new_done,
                "skipped": new_skipped,
                "failed": new_failed,
            })
            logger.info(
                f"batch_upload task {task_id} completed: "
                f"done={new_done} skipped={new_skipped} failed={new_failed}"
            )
            return


def _settle_task(task_id: str) -> None:
    """No runnable files must not leave a task looking busy forever."""
    task = metadata_db.get_upload_task(task_id)
    if not task or task["status"] in ("cancelled", "paused"):
        return
    if task["status"] in ("cancelling", "cleaning"):
        _finish_cancel(task_id)
        return
    rows = metadata_db.list_upload_task_files(task_id)
    if any(f["status"] == "queued" for f in rows):
        return
    if any(f["status"] == "processing" for f in rows):
        metadata_db.update_upload_task_progress(task_id, status="paused", current_stage="interrupted",
                                               current_percent=None, current_detail="处理已中断，等待恢复")
        _publish(task_id, {"type": "task_crashed", "task_id": task_id})
        return
    counts = {key: sum(f["status"] == status for f in rows)
              for key, status in (("done", "done"), ("skipped", "skipped"), ("failed", "failed"))}
    complete = bool(task.get("upload_complete", 1))
    metadata_db.update_upload_task_progress(task_id, **counts,
        status="completed" if complete else "uploading", current_file_path=None, current_stage=None,
        finished_at=_now_ms() if complete else None)
    if complete:
        _publish(task_id, {"type": "task_completed", "task_id": task_id, "total": task["total"], **counts})


def _run_in_thread(task_id: str) -> None:
    try:
        with _worker_lock:
            task = metadata_db.get_upload_task(task_id)
            if task and task["status"] == "running":
                _process_task(task_id)
            elif task and task["status"] in ("cancelling", "cleaning", "cleanup_failed"):
                with ingestion_lock:
                    _finish_cancel(task_id)
    except Exception:
        logger.exception("batch_upload task %s crashed", task_id)
        task = metadata_db.get_upload_task(task_id)
        if task and task["status"] in ("cancelling", "cleaning", "cleanup_failed"):
            _cleanup_failed(task_id, "后台停止，需重试清理")
        elif task and task["status"] != "cancelled":
            metadata_db.update_upload_task_progress(
                task_id, status="paused", current_stage="crashed",
                current_percent=None, current_detail="处理已中断，等待恢复")
            _publish(task_id, {"type": "task_crashed", "task_id": task_id})
    finally:
        with _registry_lock:
            # Close the race where cancel arrives after the last file returned.
            task = metadata_db.get_upload_task(task_id)
            if task and task["status"] in ("cancelling", "cleaning"):
                _finish_cancel(task_id)
            _active_workers.discard(task_id)


def start_task(task_id: str) -> None:
    """Reserve before spawning, preventing duplicate resume/retry threads."""
    import contextvars
    from src.core import accounts
    with _registry_lock:
        if task_id in _active_workers:
            return
        if accounts.enabled:
            current = accounts.user()
            accounts.execute('INSERT INTO auth_job_context VALUES (?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET user_id=excluded.user_id,provider_id=excluded.provider_id,started_at=excluded.started_at',
                             (task_id,current['id'],accounts.selected_provider.get(),_now_ms()))
            accounts.audit('upload_started', task_id)
        context = contextvars.copy_context()
        if accounts.enabled:
            context.run(accounts.identity.set, current)
        _active_workers.add(task_id)
        try:
            thread = threading.Thread(target=context.run, args=(_run_in_thread, task_id),
                                      daemon=True, name=f"batch-upload-{task_id}")
            thread.start()
        except Exception:
            _active_workers.discard(task_id)
            raise
        logger.info("batch_upload worker thread started for task %s", task_id)


def generate_task_id() -> str:
    """生成任务 ID。"""
    return f"ut_{uuid.uuid4().hex[:24]}"


def rebuild_kb(kb_id: str) -> dict[str, Any]:
    """重建 KB：删 collection + 重置文件状态 + 触发批量重新投喂。

    适用场景：embedding 模型切换后维度不匹配，需要按当前模型重新投喂。

    流程：
    1. 防并发：检查该 KB 是否已有 running task
    2. 收集所有 knowledge_files 的 chunk_ids（用于清 BM25）
    3. 删 chroma collection（向量数据丢，物理文件保留）
    4. 重置所有 knowledge_files：status=pending, chunk_ids=[], chunk_count=0, processed_at=NULL
       （status='failed' 也一并重置，让之前失败的文件也能重新尝试）
    5. 清理 BM25 索引
    6. 探测当前 embedding 维度，更新 KB 元信息 embedding_dim
    7. 创建 batch_upload task 并启动 worker

    返回 {task_id, total, new_dim}。
    """
    import json
    from src.core import vector_store, llm_client
    from src.qa import bm25_index

    kb = metadata_db.get_kb(kb_id)
    if not kb:
        return {"error": "kb_not_found"}

    # 防并发：检查是否有 running 的 task 在跑这个 KB
    active_tasks = metadata_db.list_active_upload_tasks()
    for t in active_tasks:
        if t["kb_id"] == kb_id:
            return {"error": "kb_busy", "active_task_id": t["id"]}

    collection_name = kb["collection_name"]
    files = metadata_db.list_files_in_kb(kb_id)

    # 1) 收集所有旧 chunk_ids（用于清 BM25）
    all_old_chunk_ids: list[str] = []
    for f in files:
        try:
            ids = json.loads(f.get("chunk_ids_json") or "[]")
            if isinstance(ids, list):
                all_old_chunk_ids.extend(ids)
        except Exception:
            pass

    # 2) 删 collection（向量数据丢）
    deleted_chunks = vector_store.reset_collection(collection_name)
    logger.info(
        f"rebuild KB {kb_id}: cleared collection {collection_name} "
        f"(was {deleted_chunks} chunks)"
    )

    # 3) 清 BM25 索引
    if all_old_chunk_ids:
        try:
            bm25_index.remove_chunks(all_old_chunk_ids)
            logger.info(f"rebuild KB {kb_id}: cleared {len(all_old_chunk_ids)} BM25 chunks")
        except Exception as e:
            logger.warning(f"rebuild KB {kb_id}: BM25 清理失败（不阻塞）: {e}")

    # 4) 重置所有 knowledge_files：状态 → pending，chunk_ids 清空
    #    这样 _process_task 会把它们当新文件处理
    for f in files:
        try:
            with metadata_db.get_cursor() as cur:
                cur.execute(
                    """UPDATE knowledge_files
                       SET status='pending', chunk_ids_json='[]', chunk_count=0,
                           processed_at=NULL, error_message=NULL
                       WHERE id=?""",
                    (f["id"],),
                )
        except Exception as e:
            logger.warning(f"rebuild KB {kb_id}: 重置 file {f['id']} 失败: {e}")

    # 5) 探测当前 embedding 维度，更新 KB 元信息
    new_dim: int | None = None
    new_model: str | None = None
    try:
        emb_cfg = settings.config.get("llm", {}).get("embedding", {})
        new_model = emb_cfg.get("local_model")
        vec_sample, _ = llm_client.get_client().embed(["__rebuild_probe__"])
        if vec_sample:
            new_dim = len(vec_sample[0])
    except Exception as e:
        logger.warning(f"rebuild KB {kb_id}: 探测新维度失败: {e}")
    if new_dim is not None:
        metadata_db.update_kb(kb_id, embedding_dim=new_dim, embedding_model=new_model)

    # 6) 构造 file_records（用现有的 absolute_path）
    file_records = []
    for f in files:
        abs_path = f.get("absolute_path")
        if not abs_path:
            continue
        file_records.append({
            "relative_path": f["relative_path"],
            "absolute_path": abs_path,
            "file_size": f.get("file_size", 0),
        })

    if not file_records:
        # 没有文件可重建：直接把 task 状态置为 completed
        logger.warning(f"rebuild KB {kb_id}: 没有文件可重新投喂（DB 中无记录）")
        return {"error": "no_files", "new_dim": new_dim}

    # 7) 创建 batch_upload task
    task_id = generate_task_id()
    metadata_db.create_upload_task(
        task_id=task_id,
        kb_id=kb_id,
        skip_mode="skip",  # 不重要，因为已重置为 pending
        auto_ingest=True,
        files=file_records,
    )
    start_task(task_id)

    logger.info(
        f"rebuild KB {kb_id}: created task {task_id} files={len(file_records)} new_dim={new_dim}"
    )
    return {"task_id": task_id, "total": len(file_records), "new_dim": new_dim}


def recover_interrupted_tasks() -> int:
    """启动时调用：把进程崩溃/关页留下的未完任务改 paused。

    - status='running'（worker 中断）→ paused
    - status='uploading'（分批上传没传完，页面关了）→ paused，
      继续时视为"传完收尾"（只 ingest 已到达的文件）
    """
    recovered = 0
    for run in pending_runs():
        try:
            cleanup_run(run["id"])
        except Exception as exc:
            if run["task_id"]:
                _cleanup_failed(run["task_id"], exc)
            logger.exception("interrupted ingest cleanup failed: %s", run["id"])
    for task in metadata_db.list_active_upload_tasks():
        if task["status"] in ("cancelling", "cleaning"):
            _finish_cancel(task["id"])
            recovered += 1
        elif task["status"] in ("running", "uploading"):
            stage = "interrupted" if task["status"] == "running" else "upload_interrupted"
            metadata_db.update_upload_task_progress(task["id"], status="paused", current_stage=stage,
                current_percent=None, current_detail="服务中断，等待恢复")
            recovered += 1
    return recovered


def resume_task(task_id: str) -> bool:
    """用户从 banner 点'继续'：把 paused 改 running，启动 worker。

    上传中断的任务继续时按"收尾已到达文件"处理（upload_complete=1）。
    """
    with _registry_lock:
        task = metadata_db.get_upload_task(task_id)
        if not task or task_id in _active_workers:
            return False
        if task["status"] not in ("paused", "failed", "running"):
            return False
        interrupted = metadata_db.list_upload_task_files(task_id, status="processing")
        for file in interrupted:
            metadata_db.reset_file_to_queued(file["id"])
        metadata_db.update_upload_task_progress(
            task_id, status="running", current_stage="resuming", upload_complete=True,
            current_file_path=None, current_percent=None, current_detail="等待处理，已完成文件保留",
            finished_at=None)
        start_task(task_id)
        logger.info("batch_upload resumed task=%s requeued=%s", task_id, len(interrupted))
        return True


def _cleanup_failed(task_id: str, error) -> None:
    metadata_db.update_upload_task_progress(task_id, status="cleanup_failed", current_stage="cleanup_failed",
        current_percent=None, current_detail=str(error), finished_at=None)
    _publish(task_id, {"type": "task_crashed", "task_id": task_id})


def _finish_cancel(task_id: str) -> None:
    """Only called after this task has stopped producing writes."""
    try:
        metadata_db.update_upload_task_progress(task_id, status="cleaning", current_stage="cleaning",
            current_percent=None, current_detail="正在清理本次数据，源文件保留")
        _publish(task_id, {"type": "task_cleaning", "task_id": task_id})
        committed_paths = set()
        for run in pending_runs(task_id):
            if run["status"] == "committed":
                with metadata_db.get_cursor() as cur:
                    row = cur.execute("SELECT absolute_path FROM knowledge_files WHERE id=?",(run["file_id"],)).fetchone()
                    if row: committed_paths.add(row["absolute_path"])
            cleanup_run(run["id"])
        for file in metadata_db.list_upload_task_files(task_id):
            if file["status"] in ("queued", "processing", "cleanup_failed"):
                metadata_db.update_upload_task_file_status(file["id"],
                    "done" if file["absolute_path"] in committed_paths or file.get("skip_reason") == "main_import_completed" else "cancelled", finished_at=_now_ms())
        rows = metadata_db.list_upload_task_files(task_id)
        counts = {key: sum(f["status"] == key for f in rows) for key in ("done", "skipped", "failed")}
        metadata_db.update_upload_task_progress(task_id, **counts, status="cancelled", current_stage=None,
            current_file_path=None, current_detail="已停止导入并清理临时数据；已完成文件和源文件保留", finished_at=_now_ms())
        _publish(task_id, {"type": "task_cancelled", "task_id": task_id})
    except Exception as exc:
        _cleanup_failed(task_id, exc)


def cancel_task(task_id: str) -> bool:
    """Request cooperative cancellation. A worker acknowledges only after cleanup."""
    with _registry_lock:
        task = metadata_db.get_upload_task(task_id)
        if not task or task["status"] == "completed":
            return False
        if task["status"] == "cancelled":
            return True
        metadata_db.update_upload_task_progress(task_id, status="cancelling", current_stage="cancelling",
            current_percent=None, current_detail="已请求取消，等待当前操作结束后清理；源文件保留", finished_at=None)
        _publish(task_id, {"type": "task_cancelling", "task_id": task_id})
        if task_id not in _active_workers:
            start_task(task_id)
        return True


def retry_failed_files(task_id: str, file_ids: list[int] | None = None) -> int:
    """重试 failed 文件。file_ids=None 重试所有 failed；否则只重试指定 ID。

    返回重置的文件数。重置后自动启动 worker 继续处理。
    """
    task = metadata_db.get_upload_task(task_id)
    if not task or task["status"] in ("running", "cancelling", "cleaning", "cleanup_failed"):
        return 0

    if file_ids:
        target_files = []
        for fid in file_ids:
            f = metadata_db.get_upload_task_file(fid)
            if f and f["task_id"] == task_id and f["status"] == "failed":
                target_files.append(f)
    else:
        target_files = metadata_db.list_upload_task_files(task_id, status="failed")

    for f in target_files:
        metadata_db.reset_file_to_queued(f["id"])

    # 如果任务已经完成/取消/暂停，重新启动
    if task["status"] in ("completed", "cancelled", "paused", "failed"):
        metadata_db.update_upload_task_progress(
            task_id, status="running", current_stage="retrying", finished_at=None,
            failed=max(0, task["failed"] - len(target_files)), current_percent=None, current_detail="等待重试"
        )
        start_task(task_id)

    return len(target_files)


def delete_failed_files(
    task_id: str,
    file_ids: list[int] | None = None,
    status: str = "failed",
) -> dict[str, int]:
    """删除任务下指定状态的文件（默认 failed）。

    返回 {deleted_count}。

    清理范围：
    - upload_task_files 子表行
    - knowledge_files 主表 failed 行（连带级联清理 document_meta / feedback_queue / concept 引用 / chunks）
    - 物理文件（feed 目录下的，仅在 feed_base 内才删）
    - task 对应状态计数同步减少
    """
    task = metadata_db.get_upload_task(task_id)
    if not task or task["status"] in ("running", "cancelling", "cleaning", "cleanup_failed"):
        return {"deleted_count": 0}

    if file_ids:
        target_files = []
        for fid in file_ids:
            f = metadata_db.get_upload_task_file(fid)
            if f and f["task_id"] == task_id and f["status"] == status:
                target_files.append(f)
    else:
        target_files = metadata_db.list_upload_task_files(task_id, status=status)

    kb_id = task["kb_id"]
    deleted_count = 0

    for f in target_files:
        rel_path = f["relative_path"]
        abs_path_str = f.get("absolute_path")
        # 1) 删 knowledge_files 主表对应行（带级联清理 chunks/document_meta/concepts）
        try:
            metadata_db.delete_file(rel_path, kb_id=kb_id)
        except Exception as e:
            logger.warning(f"删除 knowledge_files 失败 {rel_path}: {e}")
        # 2) 删 upload_task_files 子表行
        metadata_db.delete_upload_task_file(f["id"])
        # 3) 删物理文件（仅在 feed 目录内，避免越权删除用户其他位置的文件）
        if abs_path_str:
            try:
                abs_path = Path(abs_path_str)
                feed_base = Path(settings.feed_folder)
                # 双重检查：必须在 feed 目录内
                if abs_path.is_relative_to(feed_base) and abs_path.exists():
                    abs_path.unlink()
            except Exception as e:
                logger.warning(f"删除物理文件失败 {abs_path_str}: {e}")
        deleted_count += 1

    # 同步 task 对应状态计数
    if deleted_count > 0:
        if status == "failed":
            new_val = max(0, task["failed"] - deleted_count)
            metadata_db.update_upload_task_progress(task_id, failed=new_val)
        elif status == "skipped":
            new_val = max(0, task["skipped"] - deleted_count)
            metadata_db.update_upload_task_progress(task_id, skipped=new_val)

    _publish(task_id, {
        "type": "files_deleted",
        "task_id": task_id,
        "deleted_count": deleted_count,
    })

    return {"deleted_count": deleted_count}


def delete_kb_failed_files(kb_id: str) -> dict[str, int]:
    """删除 KB 内所有 status='failed' 的 knowledge_files（KB 级别，不依赖 task）。

    与 delete_failed_files（task 级别）的区别：
    - 这里的失败文件可能来自多次上传任务，或者通过 scan 导入
    - 不更新 task 计数（task 与 knowledge_files 是 N:N 关系，无法精确归属）
    - 物理文件仅在 feed 目录内才删

    清理范围：
    - knowledge_files 主表行
    - 级联清理 document_meta / feedback_queue / concept 引用 / chunks（通过 delete_file）
    - feed 目录下的物理文件

    返回 {deleted_count}。
    """
    kb = metadata_db.get_kb(kb_id)
    if not kb:
        return {"deleted_count": 0}

    target_files = metadata_db.list_files_in_kb(kb_id, status="failed")
    feed_base = Path(settings.feed_folder)
    deleted_count = 0

    for f in target_files:
        rel_path = f["relative_path"]
        abs_path_str = f.get("absolute_path")
        # 1) 删 knowledge_files 主表行（带级联清理）
        try:
            metadata_db.delete_file(rel_path, kb_id=kb_id)
        except Exception as e:
            logger.warning(f"删除 knowledge_files 失败 {rel_path}: {e}")
        # 2) 删物理文件（仅在 feed 目录内）
        if abs_path_str:
            try:
                abs_path = Path(abs_path_str)
                if abs_path.is_relative_to(feed_base) and abs_path.exists():
                    abs_path.unlink()
            except Exception as e:
                logger.warning(f"删除物理文件失败 {abs_path_str}: {e}")
        deleted_count += 1

    logger.info(f"KB {kb_id} 清理失败文件: {deleted_count} 个")
    return {"deleted_count": deleted_count}


# ===== 文件名清洗 =====

def safe_relative_path(raw: str) -> str:
    """清洗 webkitRelativePath / File.name 拿到的相对路径。

    - 反斜杠转正斜杠（Windows 路径）
    - 拒绝 .. / 绝对路径 / 控制字符
    - 各段做 sanitize_filename（防 .., :, 控制字符）
    """
    from src.core.security import sanitize_filename, SecurityError

    if not raw:
        raise ValueError("空路径")
    # Windows 路径转 Unix
    raw = raw.replace("\\", "/")
    # 去掉开头的 /
    if raw.startswith("/"):
        raw = raw.lstrip("/")
    parts = raw.split("/")
    safe_parts: list[str] = []
    for part in parts:
        if not part or part == ".":
            continue
        if part == "..":
            raise ValueError(f"非法路径（含 ..）: {raw}")
        try:
            safe = sanitize_filename(part)
        except SecurityError as e:
            raise ValueError(f"非法文件名 '{part}': {e}")
        safe_parts.append(safe)
    if not safe_parts:
        raise ValueError("空路径")
    return "/".join(safe_parts)
