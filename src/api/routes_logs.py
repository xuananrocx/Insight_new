"""日志查看 / 管理 API。

端点：
- GET /api/v1/logs/info - 当前日志文件信息
- GET /api/v1/logs/tail?lines=200 - 最近 N 行
- DELETE /api/v1/logs - 清空所有日志
- GET /api/v1/logs/download - 下载 zip（含所有 .log*）
"""
from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from src.core.logging_config import LOG_DIR, LOG_FILE

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])


@router.get("/info")
def logs_info() -> dict[str, Any]:
    """返回日志目录的所有文件信息。"""
    if not LOG_DIR.exists():
        return {"files": [], "total_size": 0, "oldest_mtime": None, "newest_mtime": None}

    files = []
    total = 0
    oldest: float | None = None
    newest: float | None = None
    for f in sorted(LOG_DIR.glob("app.log*"), key=lambda p: p.name):
        try:
            stat = f.stat()
        except Exception:
            continue
        files.append({
            "name": f.name,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
        })
        total += stat.st_size
        if oldest is None or stat.st_mtime < oldest:
            oldest = stat.st_mtime
        if newest is None or stat.st_mtime > newest:
            newest = stat.st_mtime

    return {
        "files": files,
        "total_size": total,
        "oldest_mtime": oldest,
        "newest_mtime": newest,
    }


@router.get("/tail")
def logs_tail(lines: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    """返回 app.log 末尾 N 行。"""
    if not LOG_FILE.exists():
        return {"lines": []}

    try:
        # 反向读取，避免大文件全读
        with open(LOG_FILE, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            block_size = 8192
            collected: list[bytes] = []
            remaining = lines
            pos = file_size
            while pos > 0 and remaining > 0:
                read_size = min(block_size, pos)
                pos -= read_size
                f.seek(pos)
                chunk = f.read(read_size)
                collected.append(chunk)
                # 简化：解析换行符
                newline_count = chunk.count(b"\n")
                if newline_count >= remaining:
                    break

            data = b"".join(reversed(collected))
            text = data.decode("utf-8", errors="replace")
            all_lines = text.splitlines()
            tail_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
            return {"lines": tail_lines}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取日志失败: {e}")


@router.delete("", status_code=204, response_model=None)
def logs_clear() -> None:
    """清空所有日志文件（删 .log* + 重建空 app.log）。"""
    if not LOG_DIR.exists():
        return
    for f in LOG_DIR.glob("app.log*"):
        try:
            f.unlink()
        except Exception:
            pass
    # 重建空 app.log（这样 RotatingFileHandler 不会因为文件消失而报错）
    LOG_FILE.touch()


@router.get("/download")
def logs_download() -> StreamingResponse:
    """下载所有 .log* 文件打包成 zip。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if LOG_DIR.exists():
            for f in sorted(LOG_DIR.glob("app.log*"), key=lambda p: p.name):
                try:
                    zf.write(f, arcname=f.name)
                except Exception:
                    pass
    buf.seek(0)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    filename = f"logs-{timestamp}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
