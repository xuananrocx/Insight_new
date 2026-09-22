"""分批上传（方案 B）后端测试：任务追加 / finalize / 启动恢复 / 取消传播。"""
from __future__ import annotations

import pytest

from src.db import metadata_db
from src.knowledge import batch_upload
from src.knowledge.ingestion import TaskCancelledError


def _mk_files(tmp_path, names):
    records = []
    for n in names:
        p = tmp_path / n
        p.write_text("hello")
        records.append({
            "relative_path": n,
            "absolute_path": str(p),
            "file_size": p.stat().st_size,
        })
    return records


def test_create_uploading_task_and_append(tmp_path):
    metadata_db.init_db()
    files1 = _mk_files(tmp_path, ["a.md", "b.md"])
    task_id = batch_upload.generate_task_id()
    metadata_db.create_upload_task(
        task_id=task_id, kb_id="default", skip_mode="skip",
        auto_ingest=True, files=files1, upload_complete=False,
    )
    t = metadata_db.get_upload_task(task_id)
    assert t["status"] == "uploading"
    assert t["upload_complete"] == 0
    assert t["total"] == 2
    # uploading 任务在 active 列表里（banner/pill 可见）
    assert any(x["id"] == task_id for x in metadata_db.list_active_upload_tasks())

    # 追加：1 新 + 1 重复
    files2 = _mk_files(tmp_path, ["c.md", "a.md"])
    appended = metadata_db.append_upload_task_files(task_id, files2)
    assert appended == 1
    t = metadata_db.get_upload_task(task_id)
    assert t["total"] == 3

    # finalize：upload_complete=1 + running
    metadata_db.update_upload_task_progress(
        task_id, status="running", current_stage="queued", upload_complete=True
    )
    t = metadata_db.get_upload_task(task_id)
    assert t["status"] == "running"
    assert t["upload_complete"] == 1


def test_recover_uploading_task_to_paused(tmp_path, monkeypatch):
    monkeypatch.setattr(batch_upload, "start_task", lambda task_id: None)
    metadata_db.init_db()
    files = _mk_files(tmp_path, ["x.md"])
    task_id = batch_upload.generate_task_id()
    metadata_db.create_upload_task(
        task_id=task_id, kb_id="default", skip_mode="skip",
        auto_ingest=True, files=files, upload_complete=False,
    )
    assert batch_upload.recover_interrupted_tasks() >= 1
    t = metadata_db.get_upload_task(task_id)
    assert t["status"] == "paused"
    assert t["current_stage"] == "upload_interrupted"

    # resume：upload_complete 补 1（按"收尾已到达文件"处理）
    assert batch_upload.resume_task(task_id) is True
    t = metadata_db.get_upload_task(task_id)
    assert t["status"] == "running"
    assert t["upload_complete"] == 1


def test_task_cancelled_error_propagates_from_progress(tmp_path, monkeypatch):
    """进度回调抛 TaskCancelledError 时应穿透 ingest_single_file（不被吞成 failed）。"""
    metadata_db.init_db()
    feed = metadata_db.settings.feed_folder if hasattr(metadata_db, "settings") else None
    from src.core.config import settings
    feed = settings.feed_folder
    feed.mkdir(parents=True, exist_ok=True)
    f = feed / "cancel_probe.md"
    f.write_text("一些内容用于解析" * 50)

    def progress(stage, payload):
        raise TaskCancelledError()

    with pytest.raises(TaskCancelledError):
        from src.knowledge import ingestion
        ingestion.ingest_single_file(
            f, kb_id="default", skip_if_exists=False, progress=progress
        )
