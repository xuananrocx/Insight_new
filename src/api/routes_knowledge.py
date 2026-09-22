"""知识库相关 API 路由。"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.core.config import settings
from src.db import metadata_db
from src.knowledge import ingestion
from src.knowledge import batch_upload
from src.knowledge.package_extractor import is_package
from src.knowledge.parsers.registry import (
    get_effective_text_extensions,
    get_supported_extensions,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


class ScanResponse(BaseModel):
    ok: bool
    result: dict[str, Any]


# ===== 扩展名白名单 =====
# 不支持的文件类型在 upload_batch 阶段就拒绝，避免写盘/入队/worker 失败的全链路浪费。
_PACKAGE_EXTENSIONS = {"zip", "tar.gz", "tgz", "tar.bz2", "tbz2", "tar.xz", "txz", "tar"}
_HIDDEN_FILE_NAMES = {".ds_store", "thumbs.db", "desktop.ini", "__macosx"}


def _get_allowed_extensions() -> set[str]:
    """返回当前允许上传的扩展名集合。

    合并来源：
    - parser 注册表（get_supported_extensions）：代码里能解析的所有类型
    - 配置项 ingest.text_extensions：用户额外声明走 text_parser 的类型
    - package 扩展名：ZIP/TAR 等压缩包（走 package_extractor）
    """
    exts = get_supported_extensions() | get_effective_text_extensions() | _PACKAGE_EXTENSIONS
    return {e.lower().lstrip(".") for e in exts if e}


def _is_hidden_or_system(name: str) -> bool:
    """判断是否为系统/隐藏垃圾文件（.DS_Store / Thumbs.db / __MACOSX 等）。"""
    lower = name.lower()
    if lower in _HIDDEN_FILE_NAMES:
        return True
    if "__macosx/" in lower.replace("\\", "/"):
        return True
    return False


def _check_extension_allowed(filename: str, allowed: set[str]) -> tuple[bool, str]:
    """返回 (allowed, reason)。

    allowed=False 时 reason 是拒绝原因；allowed=True 时 reason 为空。
    """
    if not filename:
        return False, "empty_filename"
    if _is_hidden_or_system(filename):
        return False, "hidden_system_file"
    lower = filename.lower()
    # 双扩展名（.tar.gz 等）：先检查组合
    for pkg_ext in ("tar.gz", "tar.bz2", "tar.xz"):
        if lower.endswith("." + pkg_ext):
            return (pkg_ext in allowed, "unsupported_type")
    # 单扩展名
    if "." not in lower.rsplit("/", 1)[-1]:
        return False, "no_extension"
    ext = lower.rsplit(".", 1)[-1]
    return (ext in allowed, "unsupported_type")


@router.get("/stats", response_model=dict)
def stats(kb_id: str | None = None) -> dict:
    """知识库统计。

    参数：
        kb_id: 可选，按知识库过滤；不传则统计全部
    """
    from src.core import accounts
    if accounts.enabled and not kb_id:
        result = {k:0 for k in ['files_total','files_done','files_pending','files_failed','total_chunks','total_size_bytes','feedback_pending','feedback_approved']}
        for kid in accounts.visible_kb_ids():
            for key,value in metadata_db.get_stats(kid).items():
                if not key.startswith('feedback_'):
                    result[key] = result.get(key,0) + (value or 0)
        return result
    metadata_db.init_db()
    return metadata_db.get_stats(kb_id=kb_id)


@router.post("/scan", response_model=ScanResponse)
def scan(kb_id: str = "default") -> ScanResponse:
    """手动触发文件夹扫描（增量）。

    参数：
        kb_id: 目标知识库 ID（默认 'default'）
    """
    try:
        result = ingestion.scan_feed_folder(kb_id=kb_id)
        return ScanResponse(ok=True, result=result.to_dict())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"扫描失败: {e}")


@router.post("/feed_folder/open", response_model=dict)
def open_feed_folder(kb_id: str = "default") -> dict:
    """在系统文件管理器中打开该 KB 的投喂目录（不存在则先创建）。

    目录规则与上传/扫描一致：default KB → feed 根目录，其他 KB → feed/{kb_id}/。
    """
    metadata_db.init_db()
    if not metadata_db.get_kb(kb_id):
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")

    target = settings.feed_folder if kb_id == "default" else settings.feed_folder / kb_id
    try:
        target.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            import os

            os.startfile(str(target))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except Exception as e:
        logger.exception(f"打开投喂目录失败: kb_id={kb_id}")
        raise HTTPException(status_code=500, detail=f"打开目录失败: {e}")

    return {"ok": True, "path": str(target)}


@router.get("/files", response_model=dict)
def list_files(
    status: str | None = None,
    limit: int = 200,
    kb_id: str | None = None,
) -> dict:
    """列出知识库中的文件。

    参数：
        kb_id: 可选，按知识库过滤；不传则返回全部
    """
    from src.core import accounts
    if accounts.enabled and not kb_id:
        files = []
        for kid in accounts.visible_kb_ids():
            files.extend(metadata_db.list_files(status=status,limit=min(limit,1000),kb_id=kid))
        files = files[:max(0,min(limit,1000))]
        for f in files:
            f.pop('absolute_path',None)
        return {'files':files,'count':len(files)}
    metadata_db.init_db()
    files = metadata_db.list_files(status=status, limit=limit, kb_id=kb_id)
    # 转换 chunk_ids_json 字段
    import json
    for f in files:
        f["chunk_ids"] = json.loads(f.pop("chunk_ids_json", "[]"))
        if accounts.enabled:
            f.pop('absolute_path', None)
    return {"files": files, "count": len(files)}


@router.get('/download/{file_id}')
def download_file(file_id: int, kb_id: str):
    from src.core import accounts, permissions
    permissions.require_resource('kb', kb_id, 'download')
    found = accounts.rows('SELECT absolute_path,relative_path,source_package FROM knowledge_files WHERE id=? AND kb_id=?', (file_id, kb_id))
    if not found:
        raise HTTPException(404, '文件不存在')
    target = Path(found[0]['absolute_path']).resolve()
    base = (settings.feed_folder if kb_id == 'default' else settings.feed_folder / kb_id).resolve()
    extracted = settings.get_path('extracted').resolve()
    within = target.is_relative_to(base) or (bool(found[0]['source_package']) and target.is_relative_to(extracted))
    if not within or not target.is_file():
        raise HTTPException(404, '原文件不存在或不在知识库目录中')
    if kb_id == 'default':
        # The default feed root also contains other KB directories.
        for other in accounts.rows("SELECT id FROM kbs WHERE id<>'default'"):
            if target.is_relative_to((settings.feed_folder / other['id']).resolve()):
                raise HTTPException(403, '文件不属于此知识库')
    accounts.audit('kb_file_download', f'{kb_id}:{file_id}')
    return FileResponse(target, filename=target.name, media_type='application/octet-stream')


@router.delete("/files/{rel_path:path}")
def delete_file(rel_path: str, kb_id: str | None = None) -> dict:
    """从知识库删除指定文件（按相对路径）。

    v7+ 行为：跨 KB 同名场景下应传 kb_id 精确定位；不传时删任意一条。
    """
    # 路径穿越防护：拒绝含 .. 或绝对路径
    from src.core.security import SecurityError
    if ".." in rel_path.split("/") or rel_path.startswith("/"):
        raise HTTPException(status_code=400, detail="非法路径")
    result = ingestion.remove_file_by_relpath(rel_path, kb_id=kb_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "未知错误"))
    return result


@router.get("/supported-types", response_model=dict)
def supported_types() -> dict:
    """返回支持的文件类型。"""
    return {
        "extensions": sorted(get_supported_extensions()),
        "feed_folder": str(settings.feed_folder),
    }


@router.post("/upload", response_model=dict)
async def upload_file(file: UploadFile = File(...), kb_id: str = "default") -> dict:
    """上传单个文件到 feed_folder，并立即入库。

    文件写入策略：
    - default KB → feed_folder 顶层（保持向后兼容旧数据）
    - 其他 KB → feed_folder/{kb_id}/ 子目录（避免跨 KB 同名冲突）

    参数：
        kb_id: 目标知识库 ID（默认 'default'）
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="缺少 filename")

    # 文件名穿越防护：取 basename，拒绝 ../../、绝对路径、控制字符
    from src.core.security import sanitize_filename, SecurityError
    try:
        safe_name = sanitize_filename(file.filename)
    except SecurityError as e:
        raise HTTPException(status_code=400, detail=f"非法文件名: {e}")
    if safe_name != file.filename:
        logger.info(f"文件名清洗: {file.filename!r} -> {safe_name!r}")

    feed = settings.feed_folder
    # 按 KB 分子目录存储（default 例外，保持向后兼容）
    if kb_id == "default":
        target_dir = feed / 'default'
    else:
        target_dir = feed / kb_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_name
    with target.open("wb") as f:
        while True:
            chunk = await file.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    result = ingestion.ingest_single_file(target, kb_id=kb_id)
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result)
    return result


# ===== 批量上传（v8+）=====


@router.get("/file_paths", response_model=dict)
def list_file_paths(kb_id: str) -> dict:
    """轻量端点：返回 KB 内所有 relative_path（用于前端预检同名冲突）。

    比 GET /files 轻很多（只返回路径数组），适合大批量预检。
    """
    metadata_db.init_db()
    paths = metadata_db.list_kb_file_paths(kb_id)
    return {"paths": paths, "count": len(paths)}


@router.get("/supported_extensions", response_model=dict)
def get_allowed_extensions() -> dict:
    """返回批量上传允许的文件扩展名列表。

    前端在用户选择文件后立即拉取此列表，做本地预过滤 + 弹确认框。
    数据源：parser 注册表 ∪ ingest.text_extensions 配置 ∪ package 扩展名。
    """
    return {
        "extensions": sorted(_get_allowed_extensions()),
    }


@router.post("/upload_batch", response_model=dict)
async def upload_batch(
    kb_id: str = Form(...),
    skip_mode: str = Form("skip"),
    auto_ingest: bool = Form(True),
    relative_paths: list[str] = Form([]),
    files: list[UploadFile] = File(...),
    task_id: str | None = Form(None),
    final: bool = Form(True),
) -> dict:
    """批量上传文件到指定 KB。支持分批：首批发 task_id=None & final=False 创建任务，
    后续批次带 task_id 追加，最后一批 final=True 收尾并启动 ingest。

    参数：
        kb_id: 目标知识库 ID
        skip_mode: 'skip' 跳过同名（默认）/ 'overwrite' 覆盖
        auto_ingest: 上传后是否自动 ingest
        relative_paths: 跟 files 一一对应的相对路径（用于保留子目录结构）；为空时用 filename
        files: 多个文件（multipart）
        task_id: 追加批次传已有任务 ID；首帧不传
        final: True（默认）= 一次性/收尾批次，上传完立即启动 ingest

    返回 { task_id, total, rejected }，前端用 GET /upload_tasks/{id}/stream 订阅进度。
    """
    metadata_db.init_db()

    # 验证 KB 存在
    kb = metadata_db.get_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")

    if skip_mode not in ("skip", "overwrite"):
        raise HTTPException(status_code=400, detail=f"skip_mode 必须是 skip/overwrite")

    if not files:
        raise HTTPException(status_code=400, detail="未提供文件")

    # relative_paths 必须跟 files 数量一致（如果传了）
    if relative_paths and len(relative_paths) != len(files):
        raise HTTPException(
            status_code=400,
            detail=f"relative_paths 数量({len(relative_paths)}) != files 数量({len(files)})",
        )

    feed = settings.feed_folder
    if kb_id == "default":
        target_dir = feed / 'default'
    else:
        target_dir = feed / kb_id

    # 后端兜底：再次按扩展名白名单过滤（前端不可信）
    allowed_exts = _get_allowed_extensions()

    file_records: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []

    for idx, upload_file in enumerate(files):
        # 优先用前端传的 relative_paths（保留子目录）；否则用 filename
        rel_path_raw = relative_paths[idx] if relative_paths else (upload_file.filename or "")

        try:
            rel_path = batch_upload.safe_relative_path(rel_path_raw)
        except ValueError as e:
            logger.warning(f"skip unsafe path '{rel_path_raw}': {e}")
            rejected.append({"relative_path": rel_path_raw, "reason": "unsafe_path"})
            continue

        # 扩展名白名单检查（在写盘之前）
        basename = Path(rel_path).name
        ok, reason = _check_extension_allowed(basename, allowed_exts)
        if not ok:
            logger.info(f"reject unsupported file: {rel_path} ({reason})")
            rejected.append({"relative_path": rel_path, "reason": reason})
            await upload_file.close()
            continue

        target_path = target_dir / rel_path
        if not target_path.resolve().is_relative_to(target_dir.resolve()):
            rejected.append({'relative_path': rel_path, 'reason': 'invalid_path'})
            await upload_file.close()
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with target_path.open("wb") as f:
                while True:
                    chunk = await upload_file.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
        except OSError as e:
            logger.error(f"写入文件失败 {target_path}: {e}")
            rejected.append({"relative_path": rel_path, "reason": f"write_failed: {e}"})
            continue
        finally:
            await upload_file.close()

        try:
            file_size = target_path.stat().st_size
        except OSError:
            file_size = 0

        file_records.append({
            "relative_path": rel_path,
            "absolute_path": str(target_path),
            "file_size": file_size,
        })

    if not file_records and not (task_id and final):
        # 全部被拒：返回 rejected 详情，让前端展示
        detail = "无有效文件（全部被扩展名白名单拒绝或路径非法）"
        raise HTTPException(
            status_code=400,
            detail={"message": detail, "rejected": rejected},
        )

    if task_id:
        # 追加批次：校验任务可追加
        task = metadata_db.get_upload_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
        if task["kb_id"] != kb_id:
            raise HTTPException(status_code=400, detail="task_id 与 kb_id 不匹配")
        if task["status"] != "uploading":
            raise HTTPException(
                status_code=409,
                detail=f"任务已进入 {task['status']} 状态，不能再追加文件",
            )
        appended = metadata_db.append_upload_task_files(task_id, file_records) if file_records else 0
        if final:
            metadata_db.update_upload_task_progress(
                task_id, status="running", current_stage="queued", upload_complete=True
            )
            batch_upload.start_task(task_id)
        task_total = metadata_db.get_upload_task(task_id)["total"]
        logger.info(
            f"batch_upload appended: task={task_id} appended={appended} "
            f"total={task_total} final={final} rejected={len(rejected)}"
        )
        return {
            "task_id": task_id,
            "total": task_total,
            "appended": appended,
            "rejected": rejected,
        }

    task_id = batch_upload.generate_task_id()
    metadata_db.create_upload_task(
        task_id=task_id,
        kb_id=kb_id,
        skip_mode=skip_mode,
        auto_ingest=auto_ingest,
        files=file_records,
        upload_complete=final,
    )

    if final:
        batch_upload.start_task(task_id)

    logger.info(
        f"batch_upload created: task={task_id} kb={kb_id} files={len(file_records)} "
        f"rejected={len(rejected)} skip={skip_mode} auto_ingest={auto_ingest} final={final}"
    )
    return {"task_id": task_id, "total": len(file_records), "rejected": rejected}


@router.get("/upload_tasks/active", response_model=dict)
def list_active_upload_tasks() -> dict:
    """返回所有未完成的批量上传任务（running / paused）。"""
    metadata_db.init_db()
    tasks = metadata_db.list_active_upload_tasks()
    from src.core import accounts
    if accounts.enabled:
        tasks = [t for t in tasks if accounts.owns('task', t['id']) and accounts.kb_role(t['kb_id']) in ('owner','manager','editor')]
    # 补充每个任务的 failed/skipped 文件计数（前端 banner 需要）
    for t in tasks:
        files = metadata_db.list_upload_task_files(t["id"])
        t["queued_count"] = sum(1 for f in files if f["status"] == "queued")
        t["processing_count"] = sum(1 for f in files if f["status"] == "processing")
    return {"tasks": tasks, "count": len(tasks)}


@router.get("/upload_tasks/{task_id}", response_model=dict)
def get_upload_task(task_id: str, files_status: str | None = None) -> dict:
    """返回任务详情 + 子文件列表。可选 files_status 过滤。"""
    metadata_db.init_db()
    task = metadata_db.get_upload_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    files = metadata_db.list_upload_task_files(task_id, status=files_status)
    return {**task, "files": files}


@router.post("/upload_tasks/{task_id}/retry", response_model=dict)
def retry_upload_task(task_id: str, file_ids: list[int] | None = None) -> dict:
    """重试 failed 文件。file_ids=None 重试所有 failed；传则只重试指定。"""
    metadata_db.init_db()
    task = metadata_db.get_upload_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    count = batch_upload.retry_failed_files(task_id, file_ids=file_ids or None)
    return {"ok": True, "retried_count": count}


@router.delete("/upload_tasks/{task_id}/files", response_model=dict)
def delete_task_files(
    task_id: str,
    status: str = "failed",
    file_ids: list[int] | None = None,
) -> dict:
    """删除任务下指定状态的文件（默认 failed）。

    清理范围：
    - upload_task_files 子表行
    - knowledge_files 主表对应行（连带 chunks/document_meta/concepts 引用）
    - feed 目录下的物理文件
    - task 计数同步更新

    file_ids=None 删所有匹配 status 的文件；传则只删指定 ID（仍按 status 过滤）。
    """
    metadata_db.init_db()
    task = metadata_db.get_upload_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if status not in ("failed", "skipped", "cancelled"):
        raise HTTPException(
            status_code=400,
            detail="status 只支持 failed/skipped/cancelled（禁止删 done）",
        )
    result = batch_upload.delete_failed_files(task_id, file_ids=file_ids or None)
    return {"ok": True, **result}


@router.post("/upload_tasks/{task_id}/cancel", response_model=dict)
def cancel_upload_task(task_id: str) -> dict:
    """取消未完成的文件（已完成的保留）。"""
    metadata_db.init_db()
    ok = batch_upload.cancel_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True}


@router.post("/upload_tasks/{task_id}/resume", response_model=dict)
def resume_upload_task(task_id: str) -> dict:
    """恢复 paused 任务（启动 worker 继续处理 queued 文件）。"""
    metadata_db.init_db()
    ok = batch_upload.resume_task(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail="任务不存在或状态不支持 resume")
    return {"ok": True}


@router.delete("/upload_tasks/{task_id}", response_model=dict)
def delete_upload_task(task_id: str) -> dict:
    """删除任务记录（不影响已 ingest 的 knowledge_files）。"""
    metadata_db.init_db()
    ok = metadata_db.delete_upload_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True}


@router.get("/upload_tasks/{task_id}/stream")
async def stream_upload_task(task_id: str):
    """SSE：实时推送任务进度事件。"""
    from fastapi.responses import StreamingResponse

    metadata_db.init_db()
    task = metadata_db.get_upload_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def event_stream():
        # 先推一个 snapshot 事件，让前端拿到当前状态
        snapshot = _build_task_snapshot(task_id)
        yield f"event: snapshot\ndata: {_sse_json(snapshot)}\n\n"

        # 如果任务已完成，推 done 后关闭
        if snapshot["status"] in ("completed", "cancelled"):
            yield f"event: done\ndata: {_sse_json(snapshot)}\n\n"
            return

        # 订阅事件
        queue: asyncio.Queue[dict] = asyncio.Queue()

        loop = asyncio.get_event_loop()

        def callback(event: dict) -> None:
            # 从 worker 线程把事件塞进 asyncio queue
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception:
                pass

        unsubscribe = batch_upload.subscribe(task_id, callback)

        try:
            # 推送已完成/失败文件的事件（让前端能恢复 UI 状态）
            files = metadata_db.list_upload_task_files(task_id)
            for file_info in files:
                if file_info["status"] in ("done", "skipped", "failed", "cancelled"):
                    yield f"event: file_finished\ndata: {_sse_json({'task_id': task_id, 'file_id': file_info['id'], 'relative_path': file_info['relative_path'], 'status': file_info['status'], 'skip_reason': file_info['skip_reason'], 'error': file_info['error_message'], 'done': snapshot['done'], 'skipped': snapshot['skipped'], 'failed': snapshot['failed'], 'total': snapshot['total'], 'task_finished': False})}\n\n"

            # 接收订阅事件直到任务结束
            while True:
                from src.core import accounts
                if accounts.enabled:
                    accounts.require_kb(snapshot['kb_id'], 'editor')
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    # 心跳：避免代理超时断开
                    yield ": heartbeat\n\n"
                    continue

                evt_type = event.get("type", "message")
                yield f"event: {evt_type}\ndata: {_sse_json(event)}\n\n"

                if evt_type in ("task_completed", "task_cancelled", "task_crashed"):
                    # 推 done 事件让前端关闭 stream
                    yield f"event: done\ndata: {_sse_json(event)}\n\n"
                    return
        finally:
            unsubscribe()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _build_task_snapshot(task_id: str) -> dict:
    """构造任务当前状态快照。"""
    task = metadata_db.get_upload_task(task_id)
    if not task:
        return {}
    return {
        "task_id": task_id,
        "kb_id": task["kb_id"],
        "status": task["status"],
        "total": task["total"],
        "done": task["done"],
        "skipped": task["skipped"],
        "failed": task["failed"],
        "current_file_path": task["current_file_path"],
        "current_stage": task["current_stage"],
        "auto_ingest": bool(task["auto_ingest"]),
        "skip_mode": task["skip_mode"],
    }


def _sse_json(obj: dict) -> str:
    """JSON 编码，避免嵌入换行破坏 SSE 帧。"""
    import json
    return json.dumps(obj, ensure_ascii=False, default=str).replace("\n", "\\n")
