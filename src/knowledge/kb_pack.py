"""KB Pack 导入/导出模块。

Pack 文件结构（ZIP）：
    manifest.json          # 元信息（KB 名/描述/embedding 模型/维度等）
    documents/             # 原始文档（保留 relative_path 子目录结构）
    vectors/chunks.jsonl   # 已计算好的向量（每行一个 chunk）

设计要点：
- 导出固定包含向量（不做"仅文档 pack"模式）
- BM25 索引不导出（导入时根据 chunks.jsonl 重建）
- 跨 embedding 模型导入时，提供 force_rebuild 选项重新嵌入
- 任何失败/取消 → 调用 cleanup_kb 完全回滚（删除已创建的 KB 及所有关联数据）
"""
from __future__ import annotations

import io
import json
import logging
import secrets
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from src.core import vector_store
from src.core.config import settings
from src.db import metadata_db
from src.qa import bm25_index

logger = logging.getLogger(__name__)

FORMAT_VERSION = "1.0"
EXPORTED_BY = "amd-ai-assistant"


# ===== 进度回调类型 =====

ProgressFn = Callable[[str, dict], None]
# (stage, info) -> None
# stage: "manifest" | "documents" | "vectors" | "creating_kb" | "rebuilding_embeddings" | ...
# info: {current, total, file, ...}


def _detect_zip_prefix(zf: zipfile.ZipFile) -> str:
    """检测 ZIP 内 manifest.json 的位置，返回路径前缀。

    严格规则：
    - 扁平结构（manifest.json 在根目录）→ 返回 ""
    - 1 层嵌套（唯一一层子目录里有 manifest.json，且 documents/ 和 vectors/ 在同前缀下）→ 返回 "xxx/"
    - 其他情况（找不到 manifest.json、多个 manifest.json、多层嵌套、路径不一致）→ 抛 ValueError

    设计意图：兼容 macOS 右键"压缩目录"产生的 1 层嵌套，其他异常结构明确报错让用户检查。
    """
    names = zf.namelist()
    # 过滤 macOS 元数据
    names = [n for n in names if not n.startswith("__MACOSX") and not n.endswith(".DS_Store")]

    # 找所有 manifest.json 候选
    candidates = [n for n in names if n.endswith("manifest.json")]
    if len(candidates) == 0:
        raise ValueError(
            "ZIP 中找不到 manifest.json。请确保压缩包内包含 manifest.json 文件，"
            "且位于根目录或唯一一层子目录下。"
        )
    if len(candidates) > 1:
        raise ValueError(
            f"ZIP 中找到多个 manifest.json（{len(candidates)} 个），结构异常。"
            "请检查压缩包内容。"
        )

    candidate = candidates[0]
    # 扁平结构
    if candidate == "manifest.json":
        prefix = ""
    else:
        prefix = candidate[:-len("manifest.json")]
        # 严格限制：只允许 1 层嵌套（prefix 形如 "xxx/"）
        # depth = prefix 里 / 的数量（"" = 0 层，"foo/" = 1 层，"foo/bar/" = 2 层）
        depth = prefix.count("/")
        if depth > 1:
            raise ValueError(
                f"ZIP 嵌套层级过深（{depth} 层）。请直接压缩 manifest.json + documents/ + vectors/ "
                f"三个项，或确保它们在唯一一层子目录下。当前 manifest 路径：{candidate}"
            )

    # 校验 documents/ 和 vectors/ 在同一前缀下
    expected_dirs = [f"{prefix}documents", f"{prefix}vectors"]
    for d in expected_dirs:
        # 期望存在以该前缀开头的文件（目录本身的 entries 在 ZIP 里通常是 "xxx/" 形式）
        has_content = any(
            n == f"{d}/" or n.startswith(f"{d}/")
            for n in names
        )
        if not has_content:
            raise ValueError(
                f"ZIP 结构异常：{d}/ 不存在或位置不对。"
                "请检查压缩包内 documents/ 和 vectors/ 是否跟 manifest.json 在同一层级。"
            )

    return prefix


# ===== 导出 =====


def export_kb_to_zip(
    kb_id: str,
    output_path: Path,
    progress: ProgressFn | None = None,
) -> Path:
    """导出指定 KB 为 ZIP 文件。

    返回 ZIP 文件路径。
    """
    metadata_db.init_db()
    kb = metadata_db.get_kb(kb_id)
    if not kb:
        raise ValueError(f"知识库不存在: {kb_id}")

    files = metadata_db.list_files(kb_id=kb_id, limit=100000)
    done_files = [f for f in files if f.get("status") == "done"]
    total_size = sum(f.get("file_size", 0) for f in done_files)
    total_chunks = sum(f.get("chunk_count", 0) for f in done_files)
    collection_name = kb["collection_name"]

    # KB 的 embedding 信息兜底：DB 里没设或不准时，从 Chroma 实际向量取样维度
    src_model = kb.get("embedding_model")
    src_dim = kb.get("embedding_dim")
    # 从 Chroma 实际向量取样维度（避免 metadata 跟实际不一致）
    try:
        client_chroma = vector_store.get_chroma_client()
        coll = client_chroma.get_collection(collection_name)
        if coll.count() > 0:
            sample = coll.get(limit=1, include=["embeddings"])
            embs = sample.get("embeddings")
            if embs is not None and len(embs) > 0:
                actual_dim = len(embs[0])
                if actual_dim > 0:
                    src_dim = actual_dim
    except Exception as e:
        logger.warning(f"从 Chroma 取样维度失败，用 metadata 兜底: {e}")
    # 如果还是没 model 名，从当前系统配置兜底（仅用于展示）
    if not src_model:
        from src.core import llm_client
        emb_status = llm_client.get_client().get_embedding_status()
        src_model = src_model or emb_status.get("current_model")
    # 回写 DB（修正历史脏数据）
    if src_model and src_dim:
        metadata_db.update_kb(kb_id, embedding_model=src_model, embedding_dim=src_dim)

    progress and progress("manifest", {"kb_name": kb["name"]})

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. manifest.json
        manifest: dict[str, Any] = {
            "format_version": FORMAT_VERSION,
            "exported_at": int(datetime.now(timezone.utc).timestamp()),
            "exported_by": EXPORTED_BY,
            "app_version": settings.config.get("app", {}).get("version", "unknown"),
            "kb": {
                "name": kb["name"],
                "description": kb.get("description") or "",
                "source": kb["source"],
                "embedding_model": src_model,
                "embedding_dim": src_dim,
                "original_collection_name": collection_name,
                "original_kb_id": kb_id,
            },
            "stats": {
                "document_count": len(done_files),
                "total_chunks": total_chunks,
                "total_size_bytes": total_size,
            },
            "files": [
                {
                    "relative_path": f["relative_path"],
                    "file_size": f.get("file_size", 0),
                    "file_type": f.get("file_type", ""),
                    "content_hash": f.get("content_hash", ""),
                    "chunk_count": f.get("chunk_count", 0),
                    "chunk_ids": json.loads(f.get("chunk_ids_json") or "[]"),
                }
                for f in done_files
            ],
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

        # 2. documents/
        for i, f in enumerate(done_files):
            progress and progress("documents", {
                "current": i + 1,
                "total": len(done_files),
                "file": f["relative_path"],
            })
            abs_path = Path(f["absolute_path"])
            if not abs_path.exists():
                logger.warning(f"导出时文件不存在，跳过: {abs_path}")
                continue
            arcname = f"documents/{f['relative_path']}"
            zf.write(abs_path, arcname=arcname)

        # 3. vectors/chunks.jsonl - 流式写入（避免大 KB 撑爆内存）
        progress and progress("vectors", {"current": 0, "total": total_chunks})
        written = 0
        try:
            client = vector_store.get_chroma_client()
            collection = client.get_collection(collection_name)
            # 流式写：用 zf.open 拿到文件句柄，逐行 write
            with zf.open("vectors/chunks.jsonl", "w") as fp:
                batch_size = 200
                offset = 0
                while True:
                    res = collection.get(
                        limit=batch_size,
                        offset=offset,
                        include=["documents", "embeddings", "metadatas"],
                    )
                    if not res or not res.get("ids"):
                        break
                    ids = res["ids"]
                    docs = res.get("documents", [])
                    embs = res.get("embeddings", [])
                    metas = res.get("metadatas", [])
                    for i, cid in enumerate(ids):
                        chunk_obj = {
                            "id": cid,
                            "text": docs[i] if i < len(docs) else "",
                            "vector": list(embs[i]) if i < len(embs) and embs[i] is not None else [],
                            "metadata": metas[i] if i < len(metas) else {},
                        }
                        line = json.dumps(chunk_obj, ensure_ascii=False) + "\n"
                        fp.write(line.encode("utf-8"))
                        written += 1
                        if written % 50 == 0:
                            progress and progress("vectors", {
                                "current": written,
                                "total": total_chunks,
                            })
                    offset += len(ids)
                    if len(ids) < batch_size:
                        break
        except Exception as e:
            logger.exception(f"导出向量失败: {e}")
            raise

    progress and progress("done", {"output": str(output_path)})
    logger.info(f"exported KB {kb_id} to {output_path}")
    return output_path


# ===== 预检 =====


def precheck_import(zip_path: Path) -> dict[str, Any]:
    """预检导入：返回兼容性 + 文档数等信息，不做任何写入。

    返回：
        {
          "kb_name": str,
          "kb_description": str,
          "source_embedding_model": str | None,
          "source_embedding_dim": int | None,
          "target_embedding_model": str,
          "target_embedding_dim": int,
          "compatible": bool,
          "document_count": int,
          "total_chunks": int,
        }
    """
    metadata_db.init_db()
    with zipfile.ZipFile(zip_path, "r") as zf:
        prefix = _detect_zip_prefix(zf)  # 失败时抛 ValueError
        manifest = json.loads(zf.read(prefix + "manifest.json"))
    auto_normalized = bool(prefix)

    src_model = manifest["kb"].get("embedding_model")
    src_dim = manifest["kb"].get("embedding_dim")
    stats = manifest.get("stats", {})

    # 当前系统的 embedding 模型
    from src.core import llm_client
    client = llm_client.get_client()
    emb_status = client.get_embedding_status()
    tgt_model = emb_status.get("current_model")
    tgt_dim = emb_status.get("dimensions")

    compatible = (
        src_dim is not None
        and tgt_dim is not None
        and src_dim == tgt_dim
    )

    # 检测名称是否跟现有 KB 重复
    kb_name = manifest["kb"]["name"]
    existing_names = {k["name"] for k in metadata_db.list_kbs(limit=1000)}
    is_name_duplicate = kb_name in existing_names
    # 计算兜底名（如果用户不改名，后端会用这个）
    suggested_name = kb_name
    if is_name_duplicate:
        i = 1
        while f"{kb_name} ({i})" in existing_names:
            i += 1
        suggested_name = f"{kb_name} ({i})"

    return {
        "kb_name": kb_name,
        "kb_description": manifest["kb"].get("description", ""),
        "source_embedding_model": src_model,
        "source_embedding_dim": src_dim,
        "target_embedding_model": tgt_model,
        "target_embedding_dim": tgt_dim,
        "compatible": compatible,
        "document_count": stats.get("document_count", 0),
        "total_chunks": stats.get("total_chunks", 0),
        "is_name_duplicate": is_name_duplicate,
        "suggested_name": suggested_name,
        "auto_normalized": auto_normalized,
    }


# ===== 导入 =====


def import_kb_from_zip(
    zip_path: Path,
    new_name: str | None = None,
    force_rebuild: bool = False,
    progress: ProgressFn | None = None,
) -> str:
    """从 ZIP 导入 KB，返回新 KB ID。

    参数：
        new_name: 新 KB 名称，不传则用 manifest 里的名字
        force_rebuild: 维度不兼容时是否丢弃 pack 向量、用当前模型重建
        progress: 进度回调

    任何失败都会清理已创建的数据（调 cleanup_partial_import）。
    """
    metadata_db.init_db()
    precheck = precheck_import(zip_path)
    if not precheck["compatible"] and not force_rebuild:
        raise ValueError(
            f"维度不兼容（源 {precheck['source_embedding_dim']} vs 目标 {precheck['target_embedding_dim']}），"
            f"需要 force_rebuild=true"
        )

    with zipfile.ZipFile(zip_path, "r") as zf:
        prefix = _detect_zip_prefix(zf)  # 失败时抛 ValueError
        manifest = json.loads(zf.read(prefix + "manifest.json"))
        manifest_name = manifest["kb"]["name"]
        kb_description = manifest["kb"].get("description", "")

        # 名称处理：用户传了 new_name 用之；未传且重名时自动加后缀
        if new_name:
            kb_name = new_name
        else:
            existing_names = {k["name"] for k in metadata_db.list_kbs(limit=1000)}
            if manifest_name in existing_names:
                i = 1
                while f"{manifest_name} ({i})" in existing_names:
                    i += 1
                kb_name = f"{manifest_name} ({i})"
                logger.info(f"KB 名称重复，自动改为: {kb_name}")
            else:
                kb_name = manifest_name

        # 生成新 KB ID + collection_name
        new_kb_id = f"kb_{secrets.token_hex(16)}"
        new_collection_name = f"kb_{secrets.token_hex(16)}"
        source = "imported"

        progress and progress("creating_kb", {"kb_name": kb_name})
        metadata_db.create_kb(
            kb_id=new_kb_id,
            name=kb_name,
            collection_name=new_collection_name,
            source=source,
            description=kb_description,
            embedding_model=precheck["target_embedding_model"],
            embedding_dim=precheck["target_embedding_dim"],
            is_default=False,
        )

        try:
            # 1. 解压文档到 feed_folder（专属子目录避免跨 KB 同名冲突），写入 knowledge_files 表
            progress and progress("extracting_documents", {"current": 0, "total": len(manifest["files"])})
            feed_folder = settings.feed_folder
            feed_folder.mkdir(parents=True, exist_ok=True)
            # 导入的文档放到 {feed_folder}/{new_kb_id}/ 下，跟其他 KB 隔离
            kb_feed_subdir = feed_folder / new_kb_id

            file_id_map: dict[str, int] = {}  # original_relative_path -> new file_id
            for i, f_info in enumerate(manifest["files"]):
                rel_path = f_info["relative_path"]
                arcname = f"{prefix}documents/{rel_path}"
                # P2-19: 流式读 zip（避免大文件爆内存）：zf.open 返回文件句柄，
                # shutil.copyfileobj 64KB chunk 流式写到 target
                try:
                    src_fp = zf.open(arcname)
                except KeyError:
                    logger.warning(f"pack 里缺少文件: {arcname}")
                    continue
                # ZIP Slip 防护：rel_path resolve 后必须仍在 kb_feed_subdir 内
                from src.core.security import ensure_within_path, SecurityError
                try:
                    target = ensure_within_path(Path(rel_path), kb_feed_subdir)
                except SecurityError as e:
                    logger.warning(f"跳过恶意路径 {rel_path}: {e}")
                    src_fp.close()
                    continue
                # 新 relative_path 加 kb_id 前缀，避免 ON CONFLICT(relative_path) 覆盖其他 KB 的同名文件
                new_rel_path = f"{new_kb_id}/{rel_path}"
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    import shutil as _shutil
                    with open(target, "wb") as dst_fp:
                        _shutil.copyfileobj(src_fp, dst_fp, 64 * 1024)  # 64KB chunks
                finally:
                    src_fp.close()
                # 写 DB 记录
                file_id = metadata_db.upsert_file(
                    relative_path=new_rel_path,
                    absolute_path=str(target),
                    content_hash=f_info.get("content_hash", ""),
                    file_size=f_info.get("file_size", len(data)),
                    file_type=f_info.get("file_type", ""),
                    source_package=None,
                    kb_id=new_kb_id,
                )
                file_id_map[rel_path] = file_id
                progress and progress("extracting_documents", {
                    "current": i + 1,
                    "total": len(manifest["files"]),
                    "file": rel_path,
                })

            # 2. 处理向量
            if precheck["compatible"] and not force_rebuild:
                # 直接导入向量
                _import_vectors_from_pack(zf, prefix, new_collection_name, new_kb_id, manifest["files"], file_id_map, progress)
            else:
                # 重建：用当前 embedding 模型重新 embed 文档
                _rebuild_vectors_from_documents(new_kb_id, new_collection_name, manifest, progress)

        except Exception as e:
            logger.exception(f"导入失败，开始清理: {e}")
            try:
                cleanup_partial_import(new_kb_id, new_collection_name)
            except Exception as cleanup_err:
                logger.exception(f"清理也失败: {cleanup_err}")
            raise

    progress and progress("done", {"kb_id": new_kb_id})
    logger.info(f"imported KB: {new_kb_id} ({kb_name})")
    return new_kb_id


def _import_vectors_from_pack(
    zf: zipfile.ZipFile,
    zip_prefix: str,
    collection_name: str,
    new_kb_id: str,
    new_kb_id_to_files: list[dict],
    file_id_map: dict[str, int],
    progress: ProgressFn | None,
) -> None:
    """从 pack 直接导入向量到 Chroma collection + BM25 索引。

    new_kb_id_to_files: manifest["files"] 列表，用于关联 chunk_ids 到新 file_id
    zip_prefix: ZIP 内 manifest.json 所在的目录前缀（兼容嵌套 ZIP）
    """
    # 建立 relative_path → 新 file_id 映射，以及 chunk_id → relative_path 映射
    chunk_id_to_rel: dict[str, str] = {}
    for f_info in new_kb_id_to_files:
        for cid in f_info.get("chunk_ids", []):
            chunk_id_to_rel[cid] = f_info["relative_path"]
    # file_id → chunk_ids 累加
    file_chunks: dict[int, list[str]] = {}
    for rel_path, fid in file_id_map.items():
        file_chunks[fid] = []

    with zf.open(f"{zip_prefix}vectors/chunks.jsonl") as f:
        # 流式读取（避免大文件爆内存）
        batch: list[dict[str, Any]] = []
        batch_size = 100
        total = 0
        for line in f:
            line = line.strip()
            if not line:
                continue
            chunk = json.loads(line)
            # 更新 metadata：把旧的 kb_id / file_id 替换成新的
            meta = dict(chunk.get("metadata") or {})
            meta["kb_id"] = new_kb_id
            cid = chunk["id"]
            rel = chunk_id_to_rel.get(cid)
            if rel and rel in file_id_map:
                meta["file_id"] = file_id_map[rel]
                file_chunks[file_id_map[rel]].append(cid)
            batch.append({
                "id": cid,
                "text": chunk["text"],
                "embedding": chunk["vector"],
                "metadata": meta,
            })
            if len(batch) >= batch_size:
                vector_store.upsert_chunks(batch, collection_name=collection_name)
                try:
                    bm25_index.add_chunks(batch)
                except Exception as e:
                    logger.warning(f"BM25 索引更新失败: {e}")
                total += len(batch)
                progress and progress("importing_vectors", {"current": total})
                batch = []
        if batch:
            vector_store.upsert_chunks(batch, collection_name=collection_name)
            try:
                bm25_index.add_chunks(batch)
            except Exception as e:
                logger.warning(f"BM25 索引更新失败: {e}")
            total += len(batch)
            progress and progress("importing_vectors", {"current": total})

    # 把每个文件的 chunk_ids 写回 knowledge_files 表（保持状态 done）
    for fid, chunk_ids in file_chunks.items():
        metadata_db.set_file_processed(fid, chunk_ids)


def _rebuild_vectors_from_documents(
    new_kb_id: str,
    collection_name: str,
    manifest: dict[str, Any],
    progress: ProgressFn | None,
) -> None:
    """丢弃 pack 里的向量，用当前 embedding 模型重新嵌入所有文档。"""
    from src.knowledge import ingestion

    files = metadata_db.list_files(kb_id=new_kb_id, limit=100000)
    done_files = [f for f in files if f.get("status") != "done"]
    total = len(done_files)
    progress and progress("rebuilding_embeddings", {"current": 0, "total": total})

    for i, f in enumerate(done_files):
        abs_path = Path(f["absolute_path"])
        if not abs_path.exists():
            logger.warning(f"重建时文件不存在，跳过: {abs_path}")
            continue
        ingestion.ingest_single_file(abs_path, kb_id=new_kb_id)
        progress and progress("rebuilding_embeddings", {
            "current": i + 1,
            "total": total,
            "file": f["relative_path"],
        })


def cleanup_partial_import(kb_id: str, collection_name: str) -> None:
    """清理失败的导入：删除 KB 全部痕迹。

    跟 routes_kbs.delete_kb 同样的清理范围，但本函数用于"导入中途失败"场景，
    不抛 HTTPException（不是用户主动操作）。

    清理顺序（满足 FK 约束）：
        1. document_meta（FK → knowledge_files.id）
        2. concept_relations + kb_concepts（kb_id 字段）
        3. knowledge_files（id 被前两者引用）
        4. sessions.kb_scope（设为 NULL）
        5. kbs 表记录
        6. Chroma collection
        7. BM25 索引片段
        8. feed_folder/{kb_id}/ 子目录
    """
    logger.warning(f"清理导入失败的 KB: {kb_id}")

    # 1. 收集所有 chunk_ids（用于 BM25 清理）
    try:
        files = metadata_db.list_files(kb_id=kb_id, limit=100000)
        import json as _json
        all_chunk_ids: list[str] = []
        for f in files:
            ids = _json.loads(f.get("chunk_ids_json") or "[]")
            all_chunk_ids.extend(ids)
    except Exception:
        all_chunk_ids = []

    # 2. 删 document_meta + concept_relations + kb_concepts + knowledge_files
    try:
        with metadata_db.get_cursor() as cur:
            cur.execute("DELETE FROM document_meta WHERE kb_id=?", (kb_id,))
            cur.execute("DELETE FROM concept_relations WHERE kb_id=?", (kb_id,))
            cur.execute("DELETE FROM kb_concepts WHERE kb_id=?", (kb_id,))
            cur.execute("DELETE FROM knowledge_files WHERE kb_id=?", (kb_id,))
    except Exception as e:
        logger.warning(f"清理 knowledge_files/document_meta/concepts 失败: {e}")

    # 3. 解绑 sessions.kb_scope
    try:
        with metadata_db.get_cursor() as cur:
            cur.execute("UPDATE sessions SET kb_scope=NULL WHERE kb_scope=?", (kb_id,))
    except Exception as e:
        logger.warning(f"清理 sessions.kb_scope 失败: {e}")

    # 4. 删 KB 记录
    try:
        metadata_db.delete_kb_record(kb_id)
    except Exception as e:
        logger.warning(f"清理 kbs 失败: {e}")

    # 5. 删 Chroma collection
    try:
        client = vector_store.get_chroma_client()
        client.delete_collection(collection_name)
    except Exception as e:
        logger.warning(f"清理 collection 失败: {e}")

    # 6. 删 BM25 索引片段
    if all_chunk_ids:
        try:
            bm25_index.remove_chunks(all_chunk_ids)
        except Exception as e:
            logger.warning(f"清理 BM25 失败: {e}")

    # 7. 删 feed_folder 子目录（避免孤儿文件污染后续 rebuild）
    try:
        import shutil
        from src.core.config import settings
        kb_feed_subdir = settings.feed_folder / kb_id
        if kb_feed_subdir.exists() and kb_feed_subdir.is_dir():
            shutil.rmtree(kb_feed_subdir)
    except Exception as e:
        logger.warning(f"清理 feed_folder 子目录失败: {e}")

    logger.info(f"cleanup 完成: {kb_id}")
