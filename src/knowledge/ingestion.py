"""知识库 ingestion 模块：扫描 → 解析 → 切片 → 向量化 → 入库。

完整流程（针对 feed_folder 中的每个文件）：
1. 计算文件 hash
2. 查 metadata_db：若已存在且 hash 相同 → 跳过（增量）
3. 否则：解析 → 切片 → embedding → upsert 到向量库
4. 更新 metadata_db 状态

针对程序包（.tar.gz/.zip 等）：
1. 解压到 data/extracted/
2. 对解压后的每个白名单文件，作为"子文件"走上面 1-4 流程
3. source_package 字段记录原包路径

针对已删除的文件：
- list feed_folder 中的所有文件，与 db 对比，缺失的从向量库 + db 删除
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.core import llm_client, vector_store
from src.core.config import settings
from src.db import metadata_db
from src.knowledge.chunker import Chunk, chunk_document
from src.knowledge.package_extractor import (
    extract_package,
    is_package,
    list_extractable_files,
)
from src.knowledge.parsers.base import ParsedDocument, ParseError
from src.knowledge.parsers.registry import is_supported, parse_file


# 单次扫描的进度回调
ProgressFn = Callable[[str, dict], None]


@dataclass
class ScanResult:
    """一次扫描的汇总结果。"""

    scanned: int = 0           # 扫描的总文件数（不含跳过的）
    added: int = 0             # 新增入库
    updated: int = 0           # 已存在但 hash 变了，重新入库
    skipped: int = 0           # 已存在且未变，跳过
    failed: int = 0            # 解析或入库失败
    removed: int = 0           # 已删除（从库中清理）
    extracted_packages: int = 0  # 处理的压缩包数量
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "added": self.added,
            "updated": self.updated,
            "skipped": self.skipped,
            "failed": self.failed,
            "removed": self.removed,
            "extracted_packages": self.extracted_packages,
            "errors": self.errors,
        }


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _relativize(path: Path, base: Path) -> str:
    """把绝对路径转成相对 feed_folder 的字符串（用作 db 主键）。"""
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def _embed_chunks(chunks: list[Chunk]) -> tuple[list[list[float]], str]:
    """调用 LLM 客户端批量 embedding。"""
    texts = [c.text for c in chunks]
    return llm_client.get_client().embed(texts)


def _process_one_document(
    abs_path: Path,
    rel_path: str,
    content_hash: str,
    file_size: int,
    file_type: str,
    source_package: str | None,
    result: ScanResult,
    progress: ProgressFn | None,
) -> None:
    """处理单个文档：解析 → 切片 → 向量化 → 入库。"""
    import json

    # 先查是否已存在（用于统计 added/updated 和清理旧 chunks）
    existing = metadata_db.get_file_by_path(rel_path)
    is_existing = existing is not None
    old_chunk_ids: list[str] = (
        json.loads(existing.get("chunk_ids_json") or "[]") if is_existing else []
    )

    # upsert（会重置 status=pending，清空 chunk_ids_json）
    file_id = metadata_db.upsert_file(
        relative_path=rel_path,
        absolute_path=str(abs_path),
        content_hash=content_hash,
        file_size=file_size,
        file_type=file_type,
        source_package=source_package,
    )

    # 先删旧的 chunks（如果 hash 变了，旧 chunk_id 因为带 hash，不会被新 chunk 复用）
    if old_chunk_ids:
        try:
            vector_store.delete_chunks(old_chunk_ids)
        except Exception:
            pass

    try:
        if progress:
            progress("parsing", {"file": abs_path.name, "stage": "parse"})
        doc = parse_file(abs_path)
        if progress:
            progress("chunking", {"file": abs_path.name, "stage": "chunk", "sections": len(doc.sections)})
        chunks = chunk_document(doc)
        if not chunks:
            # 文件解析后无文本（如纯图片 PDF）
            metadata_db.set_file_processed(file_id, [])
            result.skipped += 1
            return

        if progress:
            progress("embedding", {"file": abs_path.name, "stage": "embed", "chunks": len(chunks)})
        embeddings, provider = _embed_chunks(chunks)

        # 构造 chunk_id 与 payload
        chunk_payloads = []
        chunk_ids = []
        for i, (chunk, vec) in enumerate(zip(chunks, embeddings)):
            cid = vector_store.make_chunk_id(
                source_path=str(abs_path),
                content_hash=content_hash,
                chunk_index=i,
            )
            chunk_ids.append(cid)
            meta = {
                "content_hash": content_hash,
                "file_size": file_size,
                "file_id": file_id,
                "embedding_provider": provider,
                **chunk.metadata,
            }
            chunk_payloads.append({
                "id": cid,
                "text": chunk.text,
                "embedding": vec,
                "metadata": meta,
            })

        if progress:
            progress("upserting", {"file": abs_path.name, "stage": "upsert", "chunks": len(chunk_payloads)})
        vector_store.upsert_chunks(chunk_payloads)
        metadata_db.set_file_processed(file_id, chunk_ids)
        if is_existing:
            result.updated += 1
        else:
            result.added += 1
    except ParseError as e:
        result.failed += 1
        result.errors.append({"file": rel_path, "error": f"解析失败: {e}"})
        metadata_db.set_file_failed(file_id, str(e))
    except Exception as e:
        result.failed += 1
        result.errors.append({"file": rel_path, "error": f"处理失败: {e}"})
        metadata_db.set_file_failed(file_id, str(e))


def _collect_feed_files() -> list[Path]:
    """扫描 feed_folder，返回所有顶层文件 + 解压后的子文件。

    返回顺序：先非压缩包，再压缩包解压后的子文件。
    """
    feed = settings.feed_folder
    if not feed.exists():
        return []
    top_files = [p for p in feed.rglob("*") if p.is_file()]
    return top_files


def scan_feed_folder(progress: ProgressFn | None = None) -> ScanResult:
    """扫描 feed_folder，做增量同步。

    流程：
    1. 收集所有顶层文件
    2. 对压缩包：解压并扫描其子文件（白名单）
    3. 对每个文件：hash → 比对 db → 增量处理
    4. 对 db 中已不存在的文件：清理
    """
    metadata_db.init_db()
    result = ScanResult()
    feed_base = settings.feed_folder

    # 1. 收集文件
    top_files = _collect_feed_files()
    if progress:
        progress("scanning", {"top_files": len(top_files)})

    # 2. 解压所有压缩包（先解压再统一处理，便于去重）
    extracted_root = settings.get_path("extracted")
    extracted_files: list[tuple[Path, str]] = []  # (子文件路径, 所属压缩包 rel_path)
    for pkg in top_files:
        if is_package(pkg):
            try:
                if progress:
                    progress("extracting", {"package": pkg.name})
                extracted_dir = extract_package(pkg)
                rel_pkg = _relativize(pkg, feed_base)
                for sub in list_extractable_files(extracted_dir):
                    extracted_files.append((sub, rel_pkg))
                result.extracted_packages += 1
            except Exception as e:
                result.failed += 1
                result.errors.append({"file": str(pkg), "error": f"解压失败: {e}"})

    # 3. 统一处理：顶层文档 + 解压后的子文档
    all_targets: list[tuple[Path, str, str | None]] = []  # (path, rel_path, source_pkg)
    for p in top_files:
        if is_package(p):
            continue
        if not is_supported(p):
            continue
        all_targets.append((p, _relativize(p, feed_base), None))
    for sub, rel_pkg in extracted_files:
        # 子文件用 "extracted/<pkg_name>__<hash>/<relative>" 作为逻辑路径
        try:
            rel_in_extracted = sub.relative_to(extracted_root)
        except ValueError:
            rel_in_extracted = Path(sub.name)
        all_targets.append((sub, str(rel_in_extracted), rel_pkg))

    # 去重：同一 rel_path 取第一个
    seen_rel: set[str] = set()
    deduped: list[tuple[Path, str, str | None]] = []
    for item in all_targets:
        if item[1] in seen_rel:
            continue
        seen_rel.add(item[1])
        deduped.append(item)

    # 4. 逐个处理
    for abs_path, rel_path, source_pkg in deduped:
        try:
            content_hash = _hash_file(abs_path)
        except OSError as e:
            result.failed += 1
            result.errors.append({"file": rel_path, "error": f"读取失败: {e}"})
            continue
        existing = metadata_db.get_file_by_path(rel_path)
        if existing and existing.get("status") == "done" and existing.get("content_hash") == content_hash:
            result.skipped += 1
            continue
        result.scanned += 1
        _process_one_document(
            abs_path=abs_path,
            rel_path=rel_path,
            content_hash=content_hash,
            file_size=abs_path.stat().st_size,
            file_type=abs_path.suffix.lower().lstrip("."),
            source_package=source_pkg,
            result=result,
            progress=progress,
        )

    # 5. 清理：db 中存在但 feed_folder 不再有的文件
    _cleanup_removed(result, feed_base)

    return result


def _cleanup_removed(result: ScanResult, feed_base: Path) -> None:
    """删除已不在 feed_folder 的文件记录和对应 chunks。"""
    all_records = metadata_db.list_files(limit=100000)
    # 收集当前 feed 中存在的所有 rel_path（含解压子文件）
    current_rels: set[str] = set()
    if feed_base.exists():
        for p in feed_base.rglob("*"):
            if p.is_file():
                current_rels.add(_relativize(p, feed_base))
                if is_package(p):
                    try:
                        extracted_dir = extract_package(p)
                        rel_pkg = _relativize(p, feed_base)
                        for sub in list_extractable_files(extracted_dir):
                            sub_rel = str(sub.relative_to(settings.get_path("extracted")))
                            current_rels.add(sub_rel)
                    except Exception:
                        pass

    for rec in all_records:
        rel = rec["relative_path"]
        if rec.get("source_package"):
            # 来自压缩包的子文件，源包还在就不删
            pkg_rel = rec["source_package"]
            if pkg_rel in current_rels:
                continue
            # 源包被删 → 删子文件记录
        else:
            if rel in current_rels:
                continue
        # 执行删除
        try:
            chunk_ids = rec.get("chunk_ids_json") or "[]"
            import json
            ids = json.loads(chunk_ids)
            if ids:
                vector_store.delete_chunks(ids)
            metadata_db.delete_file(rel)
            result.removed += 1
        except Exception as e:
            result.failed += 1
            result.errors.append({"file": rel, "error": f"删除失败: {e}"})


def ingest_single_file(abs_path: Path) -> dict[str, Any]:
    """手动触发单个文件入库（不扫描文件夹，不做清理）。

    用于 Web UI 上传单文件时调用。
    """
    metadata_db.init_db()
    feed_base = settings.feed_folder
    rel_path = _relativize(abs_path, feed_base) if abs_path.is_relative_to(feed_base) else str(abs_path)
    try:
        content_hash = _hash_file(abs_path)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    existing = metadata_db.get_file_by_path(rel_path)
    if existing and existing.get("status") == "done" and existing.get("content_hash") == content_hash:
        return {"ok": True, "skipped": True, "file_id": existing["id"]}
    result = ScanResult()
    _process_one_document(
        abs_path=abs_path,
        rel_path=rel_path,
        content_hash=content_hash,
        file_size=abs_path.stat().st_size,
        file_type=abs_path.suffix.lower().lstrip("."),
        source_package=None,
        result=result,
        progress=None,
    )
    return {
        "ok": result.failed == 0,
        "added": result.added,
        "updated": result.updated,
        "errors": result.errors,
    }


def remove_file_by_relpath(rel_path: str) -> dict[str, Any]:
    """从库中删除指定文件（含向量 chunks）。"""
    metadata_db.init_db()
    rec = metadata_db.delete_file(rel_path)
    if not rec:
        return {"ok": False, "error": "文件不在库中"}
    import json
    chunk_ids = json.loads(rec.get("chunk_ids_json") or "[]")
    if chunk_ids:
        vector_store.delete_chunks(chunk_ids)
    return {"ok": True, "deleted_chunks": len(chunk_ids)}
