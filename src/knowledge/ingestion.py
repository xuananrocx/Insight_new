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
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.core import llm_client, vector_store
from src.core.config import settings
from src.db import metadata_db
from src.knowledge.ai_summarizer import remove_summary_data, summarize_and_extract
from src.knowledge.chunker import Chunk, chunk_document
from src.qa import bm25_index

logger = logging.getLogger(__name__)
from src.knowledge.package_extractor import (
    extract_package,
    is_package,
    list_extractable_files,
)
from src.knowledge.parsers.base import ParsedDocument, ParseError
from src.knowledge.parsers.registry import is_supported, parse_file


# 单次扫描的进度回调
ProgressFn = Callable[[str, dict], None]


class TaskCancelledError(Exception):
    """任务被取消。进度回调抛出该异常可中止当前文件的处理（阶段边界生效）。"""


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


def _infer_doc_type(path: Path) -> str:
    """根据文件名启发式判断文档类型。

    简单实现，迭代 3 可以用 LLM 重新分类。
    顺序：先按文件名关键词（更具体），再按扩展名兜底。
    """
    name = path.name.lower()
    suffix = path.suffix.lower()
    # 1. 部署/运维/手册
    if any(kw in name for kw in ["manual", "guide", "deploy", "部署", "运维", "手册", "install", "readme"]):
        return "manual"
    # 2. API 文档
    if any(kw in name for kw in ["api", "reference", "接口", "swagger", "openapi"]):
        return "api_ref"
    # 3. 测试用例
    if any(kw in name for kw in ["test", "测试", "case", "spec"]):
        return "test"
    # 4. 配置文件（扩展名兜底）
    if suffix in {".conf", ".ini", ".yaml", ".yml", ".toml", ".properties", ".env", ".cfg"}:
        return "config"
    return "unknown"


def _relativize(path: Path, base: Path) -> str:
    """把绝对路径转成相对 feed_folder 的字符串（用作 db 主键）。"""
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def _is_ai_summary_enabled() -> bool:
    """读 config: ingest.ai_summary.enabled"""
    cfg = settings.config.get("ingest", {}).get("ai_summary", {})
    return bool(cfg.get("enabled", False))


def _ai_summary_min_word_count() -> int:
    """读 config: ingest.ai_summary.min_word_count（短文档跳过 AI 处理）"""
    cfg = settings.config.get("ingest", {}).get("ai_summary", {})
    return int(cfg.get("min_word_count", 100))


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
    kb_id: str = "default",
    result: ScanResult | None = None,
    progress: ProgressFn | None = None,
) -> None:
    """处理单个文档：解析 → 切片 → 向量化 → 入库。

    参数：
        kb_id: 目标知识库 ID（默认 'default'）
        result: 统计结果对象（如果为 None，则创建新对象）
        progress: 进度回调函数
    """
    if result is None:
        result = ScanResult()
    import json

    # 先查是否已存在（用于统计 added/updated 和清理旧 chunks）
    existing = metadata_db.get_file_by_path(rel_path, kb_id=kb_id)
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
        kb_id=kb_id,
    )

    # 查询 KB 的 collection_name（用于向量库隔离）
    kb_row = metadata_db.get_kb(kb_id)
    collection_name = kb_row["collection_name"] if kb_row else vector_store.COLLECTION_NAME

    # 先删旧的 chunks（如果 hash 变了，旧 chunk_id 因为带 hash，不会被新 chunk 复用）
    if old_chunk_ids:
        try:
            vector_store.delete_chunks(old_chunk_ids, collection_name=collection_name)
        except Exception as e:
            # 向量库删除失败：旧 chunks 残留会污染检索，但不应阻塞重投喂
            logger.warning(f"vector_store.delete_chunks 失败 (collection={collection_name}, count={len(old_chunk_ids)}): {e}")
        try:
            bm25_index.remove_chunks(old_chunk_ids)
        except Exception as e:
            logger.warning(f"BM25 删除旧 chunks 失败: {e}")

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
                "kb_id": kb_id,
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
        vector_store.upsert_chunks(chunk_payloads, collection_name=collection_name)
        try:
            bm25_index.add_chunks(chunk_payloads)
        except Exception as e:
            logger.warning(f"BM25 索引更新失败: {e}")
        metadata_db.set_file_processed(file_id, chunk_ids)
        # 创建/更新文档级元信息（v4：为 RAG 升级预留）
        try:
            metadata_db.upsert_document_meta(
                file_id=file_id,
                kb_id=kb_id,
                processed_level="raw",
                doc_type=_infer_doc_type(abs_path),
                section_count=len(doc.sections),
                total_chunks=len(chunk_ids),
                word_count=sum(len(c.text) for c in chunks),
            )
        except Exception as e:
            logger.warning(f"document_meta 写入失败（不影响投喂）: {e}")
        # 迭代 2：投喂后触发 AI 摘要 + 概念提取（如启用）
        if _is_ai_summary_enabled():
            word_count = sum(len(c.text) for c in chunks)
            min_words = _ai_summary_min_word_count()
            if word_count < min_words:
                logger.info(f"跳过 AI 摘要（字数 {word_count} < {min_words}）: {abs_path.name}")
            else:
                if progress:
                    progress("ai_summary", {"file": abs_path.name, "stage": "ai_summary"})
                # 重新投喂时先清理旧的 AI 数据
                if is_existing:
                    try:
                        remove_summary_data(file_id, kb_id, content_hash)
                    except Exception as e:
                        logger.warning(f"清理旧 AI 摘要失败（不影响）: {e}")
                try:
                    ai_result = summarize_and_extract(
                        file_id=file_id,
                        kb_id=kb_id,
                        file_name=abs_path.name,
                        chunks=chunks,
                        content_hash=content_hash,
                        collection_name=collection_name,
                        source_path=str(abs_path),
                    )
                    if ai_result.ok:
                        logger.info(
                            f"AI 摘要完成: {abs_path.name} "
                            f"(concepts={len(ai_result.concepts)}, tokens={ai_result.summary_tokens}, "
                            f"provider={ai_result.model})"
                        )
                    else:
                        logger.warning(
                            f"AI 摘要失败（不影响主流程）: {abs_path.name} - {ai_result.error}"
                        )
                except Exception as e:
                    logger.warning(f"AI 摘要异常（不影响主流程）: {abs_path.name} - {e}")
        if is_existing:
            result.updated += 1
        else:
            result.added += 1
    except TaskCancelledError:
        # 进度回调检测到任务取消：原样上抛，由 worker 决定文件终态
        raise
    except ParseError as e:
        result.failed += 1
        result.errors.append({"file": rel_path, "error": f"解析失败: {e}"})
        metadata_db.set_file_failed(file_id, str(e))
    except Exception as e:
        result.failed += 1
        result.errors.append({"file": rel_path, "error": f"处理失败: {e}"})
        metadata_db.set_file_failed(file_id, str(e))


def _collect_feed_files(kb_id: str = "default") -> list[Path]:
    """扫描 feed_folder，返回该 KB 应该 ingest 的所有文件。

    按 KB 隔离：
    - default KB：扫 feed 顶层（旧数据兼容）+ feed/default/（如果有）
    - 其他 KB：只扫 feed/{kb_id}/

    注意：不会扫其他 KB 的子目录，避免跨 KB 文件污染。
    """
    feed = settings.feed_folder
    if not feed.exists():
        return []

    result: list[Path] = []
    seen: set[Path] = set()

    def _add_files_in_dir(dir_path: Path) -> None:
        if not dir_path.exists() or not dir_path.is_dir():
            return
        for p in dir_path.rglob("*"):
            if p.is_file():
                resolved = p.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    result.append(p)

    if kb_id == "default":
        # default KB：扫 feed 顶层所有文件（直接位于 feed，不在子目录里）
        for p in feed.iterdir():
            if p.is_file():
                resolved = p.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    result.append(p)
        # 也扫 feed/default/（如果存在）
        _add_files_in_dir(feed / "default")
    else:
        # 其他 KB：只扫自己的子目录
        _add_files_in_dir(feed / kb_id)

    return result


def _get_kb_feed_dirs(kb_id: str) -> list[Path]:
    """返回某 KB 在 feed_folder 下应该管理的所有目录。

    用于 _cleanup_removed。
    """
    feed = settings.feed_folder
    if kb_id == "default":
        dirs = [feed]  # 顶层（仅扫顶层文件）
        if (feed / "default").exists():
            dirs.append(feed / "default")
        return dirs
    return [feed / kb_id]


def scan_feed_folder(
    progress: ProgressFn | None = None,
    force: bool = False,
    kb_id: str = "default",
) -> ScanResult:
    """扫描 feed_folder，做增量同步。

    流程：
    1. 收集所有顶层文件
    2. 对压缩包：解压并扫描其子文件（白名单）
    3. 对每个文件：hash → 比对 db → 增量处理
    4. 对 db 中已不存在的文件：清理

    参数：
        force: True 时跳过 hash 比对，强制重新 ingest 所有文件。
                用于 embedding 维度变更后的全量重建。
        kb_id: 目标知识库 ID（默认 'default'）
    """
    metadata_db.init_db()
    result = ScanResult()
    feed_base = settings.feed_folder

    # 1. 收集文件（按 KB 隔离扫）
    top_files = _collect_feed_files(kb_id=kb_id)
    if progress:
        progress("scanning", {"top_files": len(top_files), "kb_id": kb_id, "force": force})

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
        existing = metadata_db.get_file_by_path(rel_path, kb_id=kb_id)
        if not force and existing and existing.get("status") == "done" and existing.get("content_hash") == content_hash:
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
            kb_id=kb_id,
            result=result,
            progress=progress,
        )

    # 5. 清理：db 中存在但 feed_folder 不再有的文件
    _cleanup_removed(result, feed_base, kb_id)

    return result


def _cleanup_removed(result: ScanResult, feed_base: Path, kb_id: str = "default") -> None:
    """删除已不在 feed_folder 的文件记录和对应 chunks（按 KB 隔离扫）。

    扫描规则跟 _collect_feed_files 一致：
    - default KB：扫 feed 顶层（不递归到子目录）+ feed/default/（递归）
    - 其他 KB：扫 feed/{kb_id}/（递归）

    这样不会把其他 KB 子目录的文件误当作"还存在"。
    """
    # 只处理这个 KB 的文件
    all_records = metadata_db.list_files(limit=100000, kb_id=kb_id)

    # 收集当前 KB feed 目录下存在的所有 rel_path（含解压子文件）
    current_rels: set[str] = set()

    def _scan_dir_recursively(dir_path: Path) -> None:
        if not dir_path.exists() or not dir_path.is_dir():
            return
        for p in dir_path.rglob("*"):
            if p.is_file():
                current_rels.add(_relativize(p, feed_base))
                if is_package(p):
                    try:
                        extracted_dir = extract_package(p)
                        for sub in list_extractable_files(extracted_dir):
                            sub_rel = str(sub.relative_to(settings.get_path("extracted")))
                            current_rels.add(sub_rel)
                    except Exception as e:
                        logger.warning(f"扫描投喂目录：解压 {p.name} 失败（cleanup 阶段会跳过该包的子文件）: {e}")

    def _scan_top_only(dir_path: Path) -> None:
        """只扫顶层文件（不进子目录）。"""
        if not dir_path.exists() or not dir_path.is_dir():
            return
        for p in dir_path.iterdir():
            if p.is_file():
                current_rels.add(_relativize(p, feed_base))
                if is_package(p):
                    try:
                        extracted_dir = extract_package(p)
                        for sub in list_extractable_files(extracted_dir):
                            sub_rel = str(sub.relative_to(settings.get_path("extracted")))
                            current_rels.add(sub_rel)
                    except Exception as e:
                        logger.warning(f"扫描投喂目录：解压 {p.name} 失败: {e}")

    if kb_id == "default":
        _scan_top_only(feed_base)
        _scan_dir_recursively(feed_base / "default")
    else:
        _scan_dir_recursively(feed_base / kb_id)

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
                rec_kb_id = rec.get("kb_id") or "default"
                kb_row = metadata_db.get_kb(rec_kb_id)
                cn = kb_row["collection_name"] if kb_row else vector_store.COLLECTION_NAME
                vector_store.delete_chunks(ids, collection_name=cn)
            metadata_db.delete_file(rel, kb_id=rec.get("kb_id"))
            result.removed += 1
        except Exception as e:
            result.failed += 1
            result.errors.append({"file": rel, "error": f"删除失败: {e}"})


def ingest_single_file(
    abs_path: Path,
    kb_id: str = "default",
    *,
    skip_if_exists: bool = False,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """手动触发单个文件入库（不扫描文件夹，不做清理）。

    用于 Web UI 上传单文件 / 批量上传 worker 调用。

    参数：
        kb_id: 目标知识库 ID（默认 'default'）
        skip_if_exists: True 时若 (kb_id, relative_path) 已存在则跳过（不动旧文件）；
                       False 时按 hash 比较决定是否重新 ingest（默认行为，向后兼容）
        progress: 进度回调，签名 (stage: str, payload: dict) -> None
                 stage 取值：parsing / chunking / embedding / upserting / ai_summary
    """
    metadata_db.init_db()
    feed_base = settings.feed_folder
    rel_path = _relativize(abs_path, feed_base) if abs_path.is_relative_to(feed_base) else str(abs_path)
    try:
        content_hash = _hash_file(abs_path)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    existing = metadata_db.get_file_by_path(rel_path, kb_id=kb_id)
    if existing:
        if existing.get("status") == "failed":
            logger.info(f"覆盖失败记录重新 ingest: {rel_path} (旧错误: {existing.get('error_message')})")
        elif skip_if_exists:
            return {"ok": True, "skipped": True, "file_id": existing["id"], "skip_reason": "exists"}
        elif existing.get("status") == "done" and existing.get("content_hash") == content_hash:
            return {"ok": True, "skipped": True, "file_id": existing["id"]}
    result = ScanResult()
    _process_one_document(
        abs_path=abs_path,
        rel_path=rel_path,
        content_hash=content_hash,
        file_size=abs_path.stat().st_size,
        file_type=abs_path.suffix.lower().lstrip("."),
        source_package=None,
        kb_id=kb_id,
        result=result,
        progress=progress,
    )
    return {
        "ok": result.failed == 0,
        "added": result.added,
        "updated": result.updated,
        "errors": result.errors,
    }


def remove_file_by_relpath(rel_path: str, kb_id: str | None = None) -> dict[str, Any]:
    """从库中删除指定文件（含向量 chunks）。

    v7+ 行为：传 kb_id 精确删；不传删任意一条。
    """
    metadata_db.init_db()
    rec = metadata_db.delete_file(rel_path, kb_id=kb_id)
    if not rec:
        return {"ok": False, "error": "文件不在库中"}
    import json
    chunk_ids = json.loads(rec.get("chunk_ids_json") or "[]")
    if chunk_ids:
        rec_kb_id = rec.get("kb_id") or "default"
        kb_row = metadata_db.get_kb(rec_kb_id)
        cn = kb_row["collection_name"] if kb_row else vector_store.COLLECTION_NAME
        vector_store.delete_chunks(chunk_ids, collection_name=cn)
    return {"ok": True, "deleted_chunks": len(chunk_ids)}
