"""Application runtime logs. Account audit records use /admin/audit."""
from __future__ import annotations

import io
import logging
import time
import zipfile
from collections import deque
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Response
from src.core import logging_config, permissions as p

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])


def log_files() -> list[Path]:
    return sorted(
        (f for f in logging_config.LOG_DIR.glob("app.log*") if f.is_file() and not f.is_symlink()),
        key=lambda f: f.name,
    )


@router.get("/info")
def logs_info():
    p.require("logs.view")
    files = []
    for f in log_files():
        try:
            stat = f.stat()
            files.append({"name": f.name, "size": stat.st_size, "mtime": stat.st_mtime})
        except FileNotFoundError:
            continue  # A file may rotate while we list it.
    times = [f["mtime"] for f in files]
    return {"files": files, "total_size": sum(f["size"] for f in files),
            "oldest_mtime": min(times, default=None), "newest_mtime": max(times, default=None)}


@router.get("/tail")
def logs_tail(lines: int = Query(200, ge=1, le=2000)):
    p.require("logs.view")
    try:
        # Read backwards so work depends on requested lines, not the whole file.
        with logging_config.LOG_FILE.open("rb") as stream:
            position = stream.seek(0, 2)
            chunks: deque[bytes] = deque()
            newlines = 0
            while position > 0 and newlines <= lines:
                size = min(position, 8192)
                position -= size
                stream.seek(position)
                chunk = stream.read(size)
                chunks.appendleft(chunk)
                newlines += chunk.count(b"\n")
        return {"lines": b"".join(chunks).decode("utf-8", errors="replace").splitlines()[-lines:]}
    except FileNotFoundError:
        return {"lines": []}
    except OSError as exc:
        raise HTTPException(500, "读取系统日志失败") from exc


@router.delete("", status_code=204)
def logs_clear():
    p.require("logs.clear")
    target = logging_config.LOG_FILE.resolve()
    handlers = [h for h in logging.getLogger().handlers
                if isinstance(h, logging.FileHandler) and Path(h.baseFilename).resolve() == target]
    for handler in handlers:
        handler.acquire()
    try:
        for handler in handlers:
            handler.flush()
        logging_config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        if logging_config.LOG_FILE.is_symlink():
            raise HTTPException(400, "无法清空符号链接日志文件")
        # Keep the active file in place so open handlers keep working on Windows/Linux.
        logging_config.LOG_FILE.write_bytes(b"")
        for f in log_files():
            if f != logging_config.LOG_FILE:
                f.unlink(missing_ok=True)
    except OSError as exc:
        raise HTTPException(500, "清空系统日志失败") from exc
    finally:
        for handler in reversed(handlers):
            handler.release()
    return Response(status_code=204)


@router.get("/download")
def logs_download():
    p.require("logs.view")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for f in log_files():
            try:
                archive.write(f, arcname=f.name)
            except FileNotFoundError:
                continue
    filename = f"system-logs-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
