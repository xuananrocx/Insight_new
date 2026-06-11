"""知识库相关 API 路由。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from src.core.config import settings
from src.db import metadata_db
from src.knowledge import ingestion
from src.knowledge.parsers.registry import get_supported_extensions

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


class ScanResponse(BaseModel):
    ok: bool
    result: dict[str, Any]


@router.get("/stats", response_model=dict)
def stats() -> dict:
    """知识库统计。"""
    metadata_db.init_db()
    return metadata_db.get_stats()


@router.post("/scan", response_model=ScanResponse)
def scan() -> ScanResponse:
    """手动触发文件夹扫描（增量）。"""
    try:
        result = ingestion.scan_feed_folder()
        return ScanResponse(ok=True, result=result.to_dict())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"扫描失败: {e}")


@router.get("/files", response_model=dict)
def list_files(status: str | None = None, limit: int = 200) -> dict:
    """列出知识库中的文件。"""
    metadata_db.init_db()
    files = metadata_db.list_files(status=status, limit=limit)
    # 转换 chunk_ids_json 字段
    import json
    for f in files:
        f["chunk_ids"] = json.loads(f.pop("chunk_ids_json", "[]"))
    return {"files": files, "count": len(files)}


@router.delete("/files/{rel_path:path}")
def delete_file(rel_path: str) -> dict:
    """从知识库删除指定文件（按相对路径）。"""
    result = ingestion.remove_file_by_relpath(rel_path)
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
async def upload_file(file: UploadFile = File(...)) -> dict:
    """上传单个文件到 feed_folder，并立即入库。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="缺少 filename")
    feed = settings.feed_folder
    feed.mkdir(parents=True, exist_ok=True)
    target = feed / file.filename
    with target.open("wb") as f:
        while True:
            chunk = await file.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    result = ingestion.ingest_single_file(target)
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result)
    return result
