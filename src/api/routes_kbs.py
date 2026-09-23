"""知识库（KB）管理 API。

提供 KB 的 CRUD 操作：
- 新建 KB
- 列出所有 KB
- 获取 KB 详情
- 重命名 KB
- 删除 KB（仅非 builtin 且非默认 KB）
- 导出 KB 为 Pack（ZIP）
- 导入 KB Pack（含预检和重建，SSE 流式进度）
"""
from __future__ import annotations
from src.core import kb_names

import asyncio
import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from src.core import llm_client, vector_store
from src.core.config import settings
from src.core import accounts, permissions
from src.db import metadata_db
from src.knowledge import batch_upload, kb_pack

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/kbs", tags=["kbs"])


# ===== 请求/响应模型 =====


class CreateKBRequest(BaseModel):
    scope: Literal["private", "team"] = "private"
    name: str = Field(..., min_length=1, max_length=100, description="知识库名称")
    description: str = Field("", max_length=500, description="知识库描述（可选）")


class UpdateKBRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100, description="新名称")
    description: str | None = Field(None, max_length=500, description="新描述")


class KBResponse(BaseModel):
    name_conflict: bool = False
    owner_id: str | None = None
    owner_username: str | None = None
    role: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    scope: str = "private"
    id: str
    name: str
    description: str | None
    collection_name: str
    source: str  # builtin / user / imported
    embedding_model: str | None
    embedding_dim: int | None
    is_default: bool
    created_at: int
    updated_at: int
    # 扩展字段（不在 DB 里）
    document_count: int = 0
    total_chunks: int = 0
    actual_collection_dim: int | None = None  # collection 当前实际维度（0 = 空）
    dim_mismatch: bool = False  # actual vs declared/current 是否一致
    feed_path: str = ""  # 该 KB 的投喂目录（default → feed 根，其他 → feed/{kb_id}）


# ===== API 端点 =====


def _compute_dim_mismatch(collection_name: str, declared_dim: int | None) -> tuple[int | None, bool]:
    """返回 (actual_collection_dim, dim_mismatch)。

    mismatch 判定：
    - collection 为空（actual=0）→ False（下次写入会自动锁新维度）
    - declared=None → False（无基准可比）
    - declared != actual → True
    """
    actual = vector_store.get_collection_dim(collection_name)
    if actual == 0:
        return 0, False
    if declared_dim is None:
        return actual, False
    return actual, (actual != declared_dim)


def _kb_feed_path(kb_id: str) -> str:
    """该 KB 的投喂目录（与上传/扫描的路由规则一致，仅拼路径不建目录）。"""
    feed = settings.feed_folder
    return str(feed if kb_id == "default" else feed / kb_id)


@router.get("", response_model=list[KBResponse])
def list_kbs() -> list[KBResponse]:
    """列出所有知识库（不含 documents）。"""
    kbs = metadata_db.list_kbs()

    # 为每个 KB 附加 document_count 和 total_chunks
    result = []
    for kb in kbs:
        kb_id = kb["id"]
        # 从 knowledge_files 表统计
        with metadata_db.get_cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS count, SUM(chunk_count) AS chunks
                FROM knowledge_files
                WHERE kb_id=? AND status='done'
                """,
                (kb_id,),
            )
            row = cur.fetchone()
            doc_count = row["count"] or 0
            total_chunks = row["chunks"] or 0

        actual_dim, mismatch = _compute_dim_mismatch(
            kb["collection_name"], kb.get("embedding_dim")
        )

        result.append(
            KBResponse(
                role=accounts.kb_role(kb_id) if accounts.enabled else None,
        capabilities=permissions.resource("kb",kb_id)["actions"] if accounts.enabled else permissions.KB_ACTIONS,
        scope=permissions.kb_policy(kb_id)["scope"] if accounts.enabled else "private",
                **kb_names.attribution(kb["id"]),
                id=kb["id"],
                name=kb["name"],
                description=kb.get("description"),
                collection_name=kb["collection_name"],
                source=kb["source"],
                embedding_model=kb.get("embedding_model"),
                embedding_dim=kb.get("embedding_dim"),
                is_default=bool(kb["is_default"]),
                created_at=kb["created_at"],
                updated_at=kb["updated_at"],
                document_count=doc_count,
                total_chunks=total_chunks,
                actual_collection_dim=actual_dim,
                dim_mismatch=mismatch,
                feed_path=_kb_feed_path(kb_id),
            )
        )

    for item in result:
        item.name_conflict = sum(other.name.strip().casefold() == item.name.strip().casefold()
                                 and other.scope == item.scope and (item.scope == "team" or other.owner_id == item.owner_id)
                                 for other in result) > 1
    return result


@router.get("/{kb_id}", response_model=KBResponse)
def get_kb(kb_id: str) -> KBResponse:
    """获取知识库详情（不含 documents）。"""
    kb = metadata_db.get_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")

    # 附加 document_count 和 total_chunks
    with metadata_db.get_cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS count, SUM(chunk_count) AS chunks
            FROM knowledge_files
            WHERE kb_id=? AND status='done'
            """,
            (kb_id,),
        )
        row = cur.fetchone()
        doc_count = row["count"] or 0
        total_chunks = row["chunks"] or 0

    actual_dim, mismatch = _compute_dim_mismatch(
        kb["collection_name"], kb.get("embedding_dim")
    )

    return KBResponse(
        role=accounts.kb_role(kb_id) if accounts.enabled else None,
        capabilities=permissions.resource("kb",kb_id)["actions"] if accounts.enabled else permissions.KB_ACTIONS,
        scope=permissions.kb_policy(kb_id)["scope"] if accounts.enabled else "private",
        **kb_names.attribution(kb["id"]),
        id=kb["id"],
        name=kb["name"],
        description=kb.get("description"),
        collection_name=kb["collection_name"],
        source=kb["source"],
        embedding_model=kb.get("embedding_model"),
        embedding_dim=kb.get("embedding_dim"),
        is_default=bool(kb["is_default"]),
        created_at=kb["created_at"],
        updated_at=kb["updated_at"],
        document_count=doc_count,
        total_chunks=total_chunks,
        actual_collection_dim=actual_dim,
        dim_mismatch=mismatch,
        feed_path=_kb_feed_path(kb["id"]),
    )


@router.post("", response_model=KBResponse, status_code=201)
def create_kb(req: CreateKBRequest) -> KBResponse:
    """新建知识库。

    - 自动生成唯一 kb_id（kb_<uuid>）
    - 自动创建独立 Chroma collection（kb_<uuid>）
    - source='user'（用户自建）
    """
    with metadata_db.get_cursor() as cur:
        req.name = kb_names.check(cur, req.name, scope=req.scope)
    # 生成唯一 kb_id
    kb_id = f"kb_{uuid.uuid4().hex}"
    collection_name = f"kb_{uuid.uuid4().hex}"

    # 探测当前 active embedding 模型 + 维度（用于 KB 元信息）
    embedding_model_name: str | None = None
    embedding_dim_value: int | None = None
    try:
        emb_cfg = settings.config.get("llm", {}).get("embedding", {})
        embedding_model_name = emb_cfg.get("local_model")
        vec_sample, _ = llm_client.get_client().embed(["__dim_probe__"])
        if vec_sample:
            embedding_dim_value = len(vec_sample[0])
    except Exception as e:
        logger.warning(f"创建 KB 时探测 embedding 维度失败（继续创建但不写 dim）: {e}")

    # 创建 Chroma collection
    try:
        vector_store.get_or_create_collection(collection_name)
        logger.info(f"created Chroma collection: {collection_name}")
    except Exception as e:
        logger.exception(f"failed to create Chroma collection: {collection_name}")
        raise HTTPException(status_code=500, detail=f"创建向量库失败: {e}")

    # 插入 DB
    try:
        metadata_db.create_kb(
            kb_id=kb_id,
            name=req.name,
            description=req.description,
            collection_name=collection_name,
            source="user",
            access_scope=req.scope,
            embedding_model=embedding_model_name,
            embedding_dim=embedding_dim_value,
        )
        logger.info(
            f"created KB: {kb_id} ({req.name}) model={embedding_model_name} dim={embedding_dim_value}"
        )
    except Exception as e:
        logger.exception(f"failed to create KB in DB: {kb_id}")
        # DB 失败时回滚 Chroma collection
        try:
            vector_store.reset_collection(collection_name)
            logger.info(f"rolled back Chroma collection: {collection_name}")
        except Exception:
            logger.warning(f"failed to rollback Chroma collection: {collection_name}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"创建知识库失败: {e}")

    if accounts.enabled:
        accounts.execute("INSERT OR REPLACE INTO permission_kbs VALUES (?,?,1)",(kb_id,req.scope))
    # 返回详情
    return get_kb(kb_id)


@router.post("/{kb_id}/rebuild", response_model=dict)
def rebuild_kb(kb_id: str, confirm: bool = False) -> dict:
    """重建 KB：删 collection + 重置文件状态 + 后台批量重新投喂。

    适用场景：embedding 模型切换后维度不匹配。

    必须传 confirm=true 才会执行（避免误触发）。

    流程：
    1. 检查无冲突的 running task
    2. 删 chroma collection（向量数据丢，原始文件保留）
    3. 清 BM25 索引
    4. 重置所有 knowledge_files 状态为 pending
    5. 更新 KB embedding_dim 元信息
    6. 创建 batch_upload task 并启动 worker
    7. 返回 task_id 供前端订阅 SSE 进度

    期间 KB 不可用（QA 检索会查到空 collection）。
    """
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="必须传 confirm=true 才能执行重建（破坏性操作）",
        )
    kb = metadata_db.get_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")

    result = batch_upload.rebuild_kb(kb_id)
    if "error" in result:
        err = result["error"]
        if err == "kb_not_found":
            raise HTTPException(status_code=404, detail="知识库不存在")
        if err == "kb_busy":
            raise HTTPException(
                status_code=409,
                detail=f"该 KB 已有上传任务在跑（task={result.get('active_task_id')}），请等完成后再重建",
            )
        if err == "no_files":
            return {"task_id": None, "total": 0, "new_dim": result.get("new_dim"),
                    "message": "KB 内无文件记录，仅清理了 collection"}
        raise HTTPException(status_code=500, detail=f"重建失败: {err}")
    return result


@router.put("/{kb_id}", response_model=KBResponse)
def update_kb(kb_id: str, req: UpdateKBRequest) -> KBResponse:
    """更新知识库元数据（name/description）。

    - builtin KB 不允许更新（返回 403）
    """
    kb = metadata_db.get_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")

    if kb["source"] == "builtin":
        raise HTTPException(status_code=403, detail="不允许修改内置知识库")

    # 只更新用户显式传入的字段（pydantic exclude_unset=True）
    # 区分「未传字段」（不更新）和「显式传 null」（清空）
    update_fields = req.model_dump(exclude_unset=True)
    if update_fields:
        metadata_db.update_kb(kb_id, **update_fields)

    return get_kb(kb_id)


@router.post("/{kb_id}/delete_failed_files", response_model=dict)
def delete_kb_failed_files(kb_id: str, confirm: bool = False) -> dict:
    """删除 KB 内所有 status='failed' 的文件记录（KB 级别，跨 task）。

    清理范围：
    - knowledge_files 主表行（连带 document_meta / feedback_queue / concept 引用 / chunks）
    - feed 目录下的物理文件

    与 /knowledge/upload_tasks/{task_id}/files 的区别：
    - task 级别：只删某次上传任务的失败文件
    - KB 级别（本端点）：删该 KB 内所有累积的失败文件
    """
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="必须传 confirm=true 才能执行删除（破坏性操作）",
        )
    kb = metadata_db.get_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    result = batch_upload.delete_kb_failed_files(kb_id)
    return {"ok": True, **result, "kb_id": kb_id}


@router.delete("/{kb_id}")
def delete_kb(kb_id: str) -> dict:
    """删除知识库。

    - builtin KB 不允许删除
    - 默认 KB 不允许删除
    - 同时删除 Chroma collection + DB 记录 + knowledge_files 记录 + feed_folder 子目录
    - 关联的 sessions/turns 会保留（kb_scope 会清空）
    """
    kb = metadata_db.get_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")

    if kb["source"] == "builtin":
        raise HTTPException(status_code=403, detail="不允许删除内置知识库")
    if kb["is_default"]:
        raise HTTPException(status_code=403, detail="不允许删除默认知识库")

    collection_name = kb["collection_name"]

    # 0. 收集这个 KB 下所有文件的 chunk_ids（删 BM25 索引用）
    files_in_kb = metadata_db.list_files_in_kb(kb_id)
    bm25_chunk_ids_to_remove: list[str] = []
    for f in files_in_kb:
        try:
            ids = json.loads(f.get("chunk_ids_json") or "[]")
            if isinstance(ids, list):
                bm25_chunk_ids_to_remove.extend(str(x) for x in ids if x)
        except Exception:
            pass

    # 1. 删除 knowledge_files 记录 + document_meta + 概念数据（级联）
    #    顺序：document_meta → concept_relations → kb_concepts → knowledge_files
    #    （document_meta 有 FK 引用 knowledge_files.id，必须先删）
    with metadata_db.get_cursor() as cur:
        cur.execute("DELETE FROM document_meta WHERE kb_id=?", (kb_id,))
        cur.execute("DELETE FROM concept_relations WHERE kb_id=?", (kb_id,))
        cur.execute("DELETE FROM kb_concepts WHERE kb_id=?", (kb_id,))
        cur.execute("DELETE FROM knowledge_files WHERE kb_id=?", (kb_id,))
        deleted_files = cur.rowcount

    # 2. 清空关联的 sessions 的 kb_scope
    with metadata_db.get_cursor() as cur:
        cur.execute("UPDATE sessions SET kb_scope=NULL WHERE kb_scope=?", (kb_id,))
        updated_sessions = cur.rowcount

    # 3. 删除 KB 记录
    if not metadata_db.delete_kb_record(kb_id):
        # 并发场景：另一个请求先删了；或刚校验完 source 被改了
        logger.warning(f"delete_kb_record returned False for {kb_id}（可能被并发删或权限状态变化）")

    # 4. 删除 Chroma collection
    try:
        vector_store.reset_collection(collection_name)
        logger.info(f"deleted Chroma collection: {collection_name}")
    except Exception as e:
        logger.exception(f"failed to delete Chroma collection: {collection_name}")
        raise HTTPException(status_code=500, detail=f"删除向量库失败: {e}")

    # 5. 从 BM25 索引中移除该 KB 的 chunks（避免删 KB 后 BM25 还能召回）
    if bm25_chunk_ids_to_remove:
        try:
            from src.qa import bm25_index
            bm25_index.remove_chunks(bm25_chunk_ids_to_remove)
            logger.info(f"removed {len(bm25_chunk_ids_to_remove)} chunks from BM25 index")
        except Exception as e:
            # 非致命，BM25 索引会在下次 rebuild 时重建
            logger.warning(f"failed to remove chunks from BM25: {e}")

    # 5. 删除 feed_folder 下该 KB 的子目录（避免孤儿文件污染后续 rebuild）
    import shutil
    from src.core.config import settings
    kb_feed_subdir = settings.feed_folder / kb_id
    if kb_feed_subdir.exists() and kb_feed_subdir.is_dir():
        try:
            shutil.rmtree(kb_feed_subdir)
            logger.info(f"deleted feed_folder subdir: {kb_feed_subdir}")
        except Exception as e:
            # 非致命，只记录
            logger.warning(f"failed to delete feed_folder subdir {kb_feed_subdir}: {e}")

    logger.info(
        f"deleted KB: {kb_id} ({kb['name']}), collection={collection_name}, "
        f"files={deleted_files}, sessions_updated={updated_sessions}"
    )

    return {"deleted": True, "kb_id": kb_id}


# ===== KB Pack 导出/导入 =====


@router.get("/{kb_id}/sessions")
def list_kb_sessions(kb_id: str) -> dict:
    """返回绑定到该 KB 的会话列表（按更新时间倒序）。"""
    kb = metadata_db.get_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    all_sessions = metadata_db.list_sessions(limit=1000)
    bound = [s for s in all_sessions if s.get("kb_scope") == kb_id]
    return {"sessions": bound, "count": len(bound)}


# ===== KB 概念 + 文档关联（迭代 4）=====


@router.get("/{kb_id}/concepts")
def list_kb_concepts(kb_id: str, min_mention: int = 1, limit: int = 200) -> dict:
    """返回 KB 的概念列表（用于概念表格 + 关联图）。

    参数：
        min_mention: 最小提及次数过滤（默认 1，建议 >=2 找共享概念）
        limit: 返回数量上限
    """
    kb = metadata_db.get_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")

    concepts = metadata_db.list_kb_concepts(kb_id, limit=limit)
    # 过滤 + 字段裁剪
    out = []
    for c in concepts:
        if c.get("mention_count", 0) < min_mention:
            continue
        try:
            file_ids = json.loads(c.get("source_file_ids_json") or "[]")
        except Exception:
            file_ids = []
        out.append({
            "id": c["id"],
            "name": c["concept_name"],
            "type": c.get("concept_type") or "concept",
            "description": c.get("description") or "",
            "mention_count": c["mention_count"],
            "file_ids": file_ids,
            "file_count": len(file_ids),
        })
    return {
        "kb_id": kb_id,
        "concepts": out,
        "count": len(out),
    }


@router.get("/{kb_id}/document_graph")
def get_kb_document_graph(kb_id: str, min_shared: int = 1) -> dict:
    """返回文档关联图（节点 = 文档，边 = 共享概念）。

    参数：
        min_shared: 两文档共享概念数 >= min_shared 才连边

    返回：
        {
            "nodes": [{"file_id": int, "name": str, "concept_count": int}],
            "edges": [{"source": int, "target": int, "shared_count": int, "shared_concepts": [str]}],
            "concepts_count": int,
        }
    """
    kb = metadata_db.get_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")

    # 1. 拉所有概念（含 source_file_ids）
    concepts = metadata_db.list_kb_concepts(kb_id, limit=1000)

    # 2. 建立 file_id → 概念集合的反向索引
    file_to_concepts: dict[int, list[dict]] = {}
    file_names: dict[int, str] = {}
    file_paths: dict[int, str] = {}

    # 拉 KB 下所有文档元信息（取文档名）
    files = metadata_db.list_files(kb_id=kb_id, limit=1000)
    for f in files:
        fid = f["id"]
        rel_path = f.get("relative_path", "")
        # 取文件名（最后一段）
        name = rel_path.replace("\\", "/").split("/")[-1] or rel_path or f"file #{fid}"
        file_names[fid] = name
        file_paths[fid] = rel_path

    for c in concepts:
        try:
            fids = json.loads(c.get("source_file_ids_json") or "[]")
        except Exception:
            fids = []
        for fid in fids:
            file_to_concepts.setdefault(fid, []).append({
                "id": c["id"],
                "name": c["concept_name"],
                "type": c.get("concept_type") or "concept",
            })

    # 3. 构造节点：只在 file_to_concepts 里出现的文档（有概念的）
    nodes = [
        {
            "file_id": fid,
            "name": file_names.get(fid, f"file #{fid}"),
            "path": file_paths.get(fid, ""),
            "concept_count": len(cs),
            "concepts": [c["name"] for c in cs],
        }
        for fid, cs in file_to_concepts.items()
    ]
    nodes.sort(key=lambda x: -x["concept_count"])

    # 4. 构造边：两两文档共享概念 >= min_shared
    edges = []
    fids_list = list(file_to_concepts.keys())
    for i in range(len(fids_list)):
        for j in range(i + 1, len(fids_list)):
            fid_a = fids_list[i]
            fid_b = fids_list[j]
            names_a = {c["name"] for c in file_to_concepts[fid_a]}
            names_b = {c["name"] for c in file_to_concepts[fid_b]}
            shared = names_a & names_b
            if len(shared) >= min_shared:
                edges.append({
                    "source": fid_a,
                    "target": fid_b,
                    "shared_count": len(shared),
                    "shared_concepts": sorted(shared),
                })

    # 边按 shared_count 倒序
    edges.sort(key=lambda x: -x["shared_count"])

    return {
        "kb_id": kb_id,
        "nodes": nodes,
        "edges": edges,
        "concepts_count": len(concepts),
    }


@router.get("/{kb_id}/export")
def export_kb(kb_id: str) -> Any:
    """导出 KB 为 ZIP Pack（流式下载）。"""
    kb = metadata_db.get_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")

    # 用临时文件存 ZIP，下载完自动删
    fd, tmp_name = tempfile.mkstemp(suffix=".zip", prefix=f"kbpack_{kb_id}_")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        kb_pack.export_kb_to_zip(kb_id, tmp)
        if accounts.enabled:
            permissions.require_resource("kb",kb_id,"export")
            accounts.audit("kb_export",kb_id)
        # 文件名：{kb_name}_{YYYYMMDD_HHMMSS}.zip
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in kb["name"])[:50]
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        download_name = f"{safe_name}_{timestamp}.zip"
        return FileResponse(
            path=str(tmp),
            media_type="application/zip",
            filename=download_name,
            # 用 background task 在响应完成后删除临时文件
            background=BackgroundTask(tmp.unlink, missing_ok=True),
        )
    except Exception as e:
        logger.exception(f"导出 KB 失败: {e}")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"导出失败: {e}")


@router.post("/import")
async def import_kb(
    file: UploadFile = File(...),
    new_name: str | None = None,
    check_only: bool = False,
    force_rebuild: bool = False,
) -> dict:
    """导入 KB Pack 预检端点。

    - check_only=true：返回预检结果（兼容性、文档数、名称重复检测等）
    - 不兼容且未允许重建：返回 needs_rebuild=true，前端弹重建确认
    - 兼容或允许重建：本端点不实际导入，前端改调 /import_stream 获取实时进度

    参数：
        file: ZIP 文件
        new_name: 可选，新 KB 名称
        check_only: 只预检
        force_rebuild: 维度不兼容时是否强制重建
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="缺少 filename")

    # 接收文件到临时路径
    suffix = Path(file.filename).suffix or ".zip"
    tmp = Path(tempfile.mkstemp(suffix=suffix)[1])
    try:
        with tmp.open("wb") as f:
            while True:
                chunk = await file.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)

        # 预检
        try:
            precheck = kb_pack.precheck_import(tmp)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Pack 解析失败: {e}")

        if check_only:
            return {"check_only": True, **precheck}

        # 不兼容且未允许重建 → 让前端弹重建确认
        if not precheck["compatible"] and not force_rebuild:
            return {
                "check_only": False,
                "imported": False,
                "needs_rebuild": True,
                **precheck,
            }

        # 兼容或允许重建 → 提示前端改用 /import_stream
        return {
            "check_only": False,
            "imported": False,
            "ready_to_stream": True,
            **precheck,
        }
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


@router.post("/import_stream")
async def import_kb_stream(
    file: UploadFile = File(...),
    new_name: str | None = None,
    force_rebuild: bool = False,
) -> StreamingResponse:
    """导入 KB Pack（SSE 流式进度）。

    前端用 EventSource 监听 stage / progress / done / error 事件。
    前端 abort → 后端检测 client disconnect → cleanup_partial_import 回滚。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="缺少 filename")

    # 先接收完整文件到临时路径
    suffix = Path(file.filename).suffix or ".zip"
    tmp = Path(tempfile.mkstemp(suffix=suffix)[1])
    with tmp.open("wb") as f:
        while True:
            chunk = await file.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)

    # 预检（再次，因为 stream 是独立请求）
    try:
        precheck = kb_pack.precheck_import(tmp)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Pack 解析失败: {e}")

    compatible = precheck["compatible"]
    will_rebuild = force_rebuild or not compatible

    # 异步生成 SSE 事件流
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()
    created_kb_id: list[str | None] = [None]  # 用 list 做可变闭包
    created_collection_name: list[str | None] = [None]

    def progress_cb(stage: str, info: dict) -> None:
        # 同步回调（来自 to_thread），需要 call_soon_threadsafe 投递
        try:
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"type": "progress", "data": {"stage": stage, **info}},
            )
        except Exception:
            pass

    async def producer() -> None:
        try:
            # 发开始事件
            await queue.put({
                "type": "stage",
                "data": {
                    "stage": "starting",
                    "compatible": compatible,
                    "will_rebuild": will_rebuild,
                    "document_count": precheck["document_count"],
                    "total_chunks": precheck["total_chunks"],
                },
            })

            # 在线程里跑同步导入
            def run_import() -> str:
                return kb_pack.import_kb_from_zip(
                    tmp,
                    new_name=new_name,
                    force_rebuild=force_rebuild,
                    progress=progress_cb,
                )

            new_kb_id = await asyncio.to_thread(run_import)
            created_kb_id[0] = new_kb_id
            # 查 collection_name（用于 cleanup）
            kb_row = metadata_db.get_kb(new_kb_id)
            if kb_row:
                created_collection_name[0] = kb_row["collection_name"]

            # 查实际名字
            actual_name = kb_row["name"] if kb_row else precheck["kb_name"]
            await queue.put({
                "type": "done",
                "data": {
                    "kb_id": new_kb_id,
                    "kb_name": actual_name,
                    "rebuilt": will_rebuild,
                    "auto_normalized": precheck.get("auto_normalized", False),
                },
            })
        except asyncio.CancelledError:
            # 前端断开
            await queue.put({"type": "cancelled", "data": {"reason": "client_disconnected"}})
            await cleanup_after_cancel(created_kb_id[0], created_collection_name[0])
            raise
        except Exception as e:
            logger.exception(f"import_stream 失败: {e}")
            await queue.put({"type": "error", "data": {"message": str(e)}})
            await cleanup_after_cancel(created_kb_id[0], created_collection_name[0])
        finally:
            await queue.put(None)
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    async def cleanup_after_cancel(kb_id: str | None, collection_name: str | None) -> None:
        if kb_id:
            try:
                # 复用 kb_pack 的清理（删 KB 记录 + knowledge_files + collection + BM25）
                cn = collection_name or ""
                await asyncio.to_thread(kb_pack.cleanup_partial_import, kb_id, cn)
                logger.info(f"cleanup_after_cancel: {kb_id}")
            except Exception as e:
                logger.warning(f"cleanup_after_cancel 失败: {e}")

    producer_task = asyncio.create_task(producer())

    async def event_generator():
        try:
            while True:
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield b": ping\n\n"
                    continue
                if evt is None:
                    break
                evt_type = evt.get("type", "message")
                data = evt.get("data", {})
                payload = f"event: {evt_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                yield payload.encode("utf-8")
                if evt_type in ("done", "error", "cancelled"):
                    break
        finally:
            if not producer_task.done():
                producer_task.cancel()
                try:
                    await producer_task
                except BaseException:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )



# ===== KB 全局摘要（迭代 6）=====


class KbGlobalSummaryResponse(BaseModel):
    kb_id: str
    has_summary: bool
    summary: str | None = None
    model: str | None = None
    tokens: int | None = None
    created_at: int | None = None
    doc_count: int | None = None
    stale: bool = False  # 生成后 KB 文件集有变化（新增/变更/删除）→ 摘要可能过期


@router.get("/{kb_id}/global_summary", response_model=KbGlobalSummaryResponse)
def get_kb_global_summary_api(kb_id: str) -> KbGlobalSummaryResponse:
    """读取 KB 全局摘要。"""
    kb = metadata_db.get_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    data = metadata_db.get_kb_global_summary(kb_id)
    if not data:
        return KbGlobalSummaryResponse(kb_id=kb_id, has_summary=False)
    return KbGlobalSummaryResponse(
        kb_id=kb_id,
        has_summary=True,
        summary=data["summary"],
        model=data["model"],
        tokens=data["tokens"],
        created_at=data["created_at"],
        stale=metadata_db.is_kb_summary_stale(kb_id),
    )


@router.post("/{kb_id}/global_summary/build", response_model=KbGlobalSummaryResponse)
def build_kb_global_summary(kb_id: str, force: bool = False) -> KbGlobalSummaryResponse:
    """生成/更新 KB 全局摘要。"""
    if accounts.enabled:
        permissions.require_resource("kb", kb_id, "manage")
    from src.knowledge.ai_summarizer import generate_kb_global_summary
    kb = metadata_db.get_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    result = generate_kb_global_summary(kb_id, force=force)
    if not result.ok:
        if result.error == "already_exists":
            # 已有，返回现有的
            data = metadata_db.get_kb_global_summary(kb_id)
            return KbGlobalSummaryResponse(
                kb_id=kb_id,
                has_summary=True,
                summary=data["summary"] if data else "",
                model=data["model"] if data else None,
                tokens=data["tokens"] if data else None,
                created_at=data["created_at"] if data else None,
            )
        raise HTTPException(status_code=500, detail=f"生成失败: {result.error}")
    return KbGlobalSummaryResponse(
        kb_id=kb_id,
        has_summary=True,
        summary=result.summary,
        model=result.model,
        tokens=result.tokens,
        doc_count=result.doc_count,
    )


@router.delete("/{kb_id}/global_summary", response_model=dict)
def delete_kb_global_summary(kb_id: str) -> dict:
    """删除 KB 全局摘要。"""
    if accounts.enabled:
        permissions.require_resource("kb", kb_id, "manage")
    kb = metadata_db.get_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    metadata_db.update_kb_global_summary(kb_id, summary="", model="", tokens=0)
    return {"deleted": True, "kb_id": kb_id}
