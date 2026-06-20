"""BM25 索引：关键词检索（中文用 jieba 分词）。

与向量检索互补：
- 向量检索：语义匹配（"行情丢包" ↔ "Sequence Gap"）
- BM25：关键词精确匹配（错误码、配置项、专有名词）

ingest 时同步更新；持久化到 data/bm25_index.json。

历史格式 bm25_index.pkl（pickle）已弃用：pickle 有任意代码执行风险，
本地用户改写该文件即可在问答启动时执行任意代码。
"""
from __future__ import annotations

import json
import logging
import os
import pickle
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _get_index_file() -> Path:
    """bm25 索引文件路径（位于 metadata.db 同目录下）。"""
    from src.core.config import settings
    return settings.get_path("metadata_db").parent / "bm25_index.json"


def _get_legacy_index_file() -> Path:
    """旧 pkl 文件路径（仅用于一次性迁移）。"""
    from src.core.config import settings
    return settings.get_path("metadata_db").parent / "bm25_index.pkl"


# 全局状态（避免每次查询都加载）
_state: dict[str, Any] | None = None

# 全局锁：保护 _state 的 add/remove/clear/query 互斥（避免撕裂读）
_state_lock: Any  # 延迟 import 后赋值
import threading
_state_lock = threading.RLock()


def _tokenize(text: str) -> list[str]:
    """中文分词 + 过滤空白。"""
    import jieba
    tokens: list[str] = []
    for tok in jieba.lcut_for_search(text):
        tok = tok.strip()
        if not tok:
            continue
        if tok in {" ", "\n", "\t", "\r", "。", "，", "、", "；"}:
            continue
        tokens.append(tok.lower())
    return tokens


def _state_load() -> dict[str, Any]:
    global _state
    if _state is not None:
        return _state

    with _state_lock:
        # double-check：拿到锁后再检查一次
        if _state is not None:
            return _state

        payload: dict[str, Any] = {"chunk_ids": [], "corpus": [], "chunks": []}
        index_file = _get_index_file()
        if index_file.exists():
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                payload.update({
                    "chunk_ids": loaded.get("chunk_ids", []),
                    "corpus": loaded.get("corpus", []),
                    "chunks": loaded.get("chunks", []),
                })
            except Exception as e:
                logger.warning(f"BM25 索引加载失败，重建空索引: {e}")
        else:
            # 一次性迁移：旧 .pkl 格式（仅迁移用户已运行过旧版本的数据）
            legacy = _get_legacy_index_file()
            if legacy.exists():
                try:
                    # pickle 仍有风险，但只在用户已有的本地文件上跑一次
                    # 攻击窗口仅限迁移这一刻，迁移后删除 .pkl
                    with open(legacy, "rb") as f:
                        loaded = pickle.load(f)
                    payload.update({
                        "chunk_ids": loaded.get("chunk_ids", []),
                        "corpus": loaded.get("corpus", []),
                        "chunks": loaded.get("chunks", []),
                    })
                    # 立刻保存新格式 + 删旧
                    _state = payload
                    _save()
                    try:
                        legacy.unlink()
                        logger.info(f"已迁移 BM25 索引: {legacy.name} -> {index_file.name}")
                    except Exception as e:
                        logger.warning(f"删除旧 pkl 失败: {e}")
                except Exception as e:
                    logger.warning(f"BM25 旧 pkl 迁移失败: {e}")

        payload["bm25"] = None
        _state = payload

        if _state["corpus"]:
            _rebuild_bm25()
        return _state


def _rebuild_bm25() -> None:
    """重建 BM25 索引。

    优化：BM25Okapi 构造 O(n) 在大 corpus 上耗时数秒，不持锁避免阻塞 query。
    持锁期间只做 corpus 引用复制，构造完成后短暂持锁替换引用。
    """
    from rank_bm25 import BM25Okapi
    s = _state_load()
    # 阶段 1：持锁做 corpus 快照（list 浅拷贝）
    with _state_lock:
        corpus_snapshot = list(s["corpus"])
    # 阶段 2：锁外构造 BM25Okapi（耗时操作）
    new_bm25 = BM25Okapi(corpus_snapshot) if corpus_snapshot else None
    # 阶段 3：短暂持锁替换引用
    with _state_lock:
        s["bm25"] = new_bm25


def _save() -> None:
    s = _state_load()
    index_file = _get_index_file()
    index_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "chunk_ids": s["chunk_ids"],
        "corpus": s["corpus"],
        "chunks": s["chunks"],
    }
    # 写临时文件 + rename 保证原子性
    tmp_file = index_file.with_suffix(index_file.suffix + ".tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp_file, index_file)
    # 敏感文件收紧权限
    try:
        os.chmod(index_file, 0o600)
    except OSError:
        pass


def add_chunks(chunks: list[dict[str, Any]]) -> None:
    """新增 chunks 到 BM25 索引（线程安全）。

    chunks: [{"id": "...", "text": "...", "metadata": {...}}]，
    内部存储拍平为 {"id", "text", **metadata}，与 vector_store 返回结构一致。

    P2-21: 优化 O(n²) → O(n)：一次扫描收集要替换的 cid，批量 _remove_ids
    """
    if not chunks:
        return
    s = _state_load()
    with _state_lock:
        existing = set(s["chunk_ids"])
        # 一次性收集要移除的 cid（已存在的），批量移除
        to_replace = {c["id"] for c in chunks if c["id"] in existing}
        if to_replace:
            _remove_ids(to_replace)
        # 批量追加
        for c in chunks:
            cid = c["id"]
            meta = c.get("metadata", {}) or {}
            flat = {"id": cid, "text": c["text"], **meta}
            s["chunk_ids"].append(cid)
            s["corpus"].append(_tokenize(c["text"]))
            s["chunks"].append(flat)
    _rebuild_bm25()
    _save()


def _remove_ids(id_set: set[str]) -> None:
    """从 _state 移除指定 ids（调用方负责加锁）。"""
    s = _state_load()
    keep = [(cid, corp, ch) for cid, corp, ch
            in zip(s["chunk_ids"], s["corpus"], s["chunks"])
            if cid not in id_set]
    s["chunk_ids"] = [k[0] for k in keep]
    s["corpus"] = [k[1] for k in keep]
    s["chunks"] = [k[2] for k in keep]


def remove_chunks(chunk_ids: list[str]) -> None:
    """从 BM25 索引删除 chunks（线程安全）。"""
    if not chunk_ids:
        return
    _state_load()
    with _state_lock:
        _remove_ids(set(chunk_ids))
    _rebuild_bm25()
    _save()


def clear() -> None:
    """清空 BM25 索引（用于重建向量库时）。"""
    global _state
    with _state_lock:
        _state = {"chunk_ids": [], "corpus": [], "chunks": [], "bm25": None}
    _save()


def query(
    query_text: str,
    top_k: int = 20,
    kb_id: str | None = None,
) -> list[dict[str, Any]]:
    """BM25 查询，返回 top-K chunks（带 bm25_score）。

    参数：
        kb_id: 可选，按知识库过滤；不传则不过滤（兼容旧数据）

    返回的 chunk 是 dict，含 id/text/metadata/bm25_score。

    线程安全：scores 计算期间持锁，避免与 add/remove 并发导致索引撕裂。
    优化：持锁期间只做引用快照（bm25 + chunks），锁外做 scores 计算和排序。
    """
    s = _state_load()
    with _state_lock:
        if s["bm25"] is None or not s["chunk_ids"]:
            return []
        bm25_ref = s["bm25"]
        chunks_snapshot = list(s["chunks"])

    # 锁外做 tokens + scores（O(n) 计算）
    tokens = _tokenize(query_text)
    if not tokens:
        return []
    scores = bm25_ref.get_scores(tokens)

    # 持锁外做排序 + 过滤（基于快照）
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
    out: list[dict[str, Any]] = []
    # 当指定 kb_id 时，需要扩大候选集以保证过滤后仍有 top_k 条
    scan_limit = len(ranked) if kb_id else top_k
    for i, score in ranked[:scan_limit]:
        if score <= 0:
            break
        chunk = dict(chunks_snapshot[i])
        if kb_id is not None:
            chunk_kb = chunk.get("kb_id") or "default"
            if chunk_kb != kb_id:
                continue
        chunk["bm25_score"] = float(score)
        out.append(chunk)
        if len(out) >= top_k:
            break
    return out


def stats() -> dict[str, Any]:
    """返回索引统计。"""
    s = _state_load()
    index_file = _get_index_file()
    return {
        "chunk_count": len(s["chunk_ids"]),
        "persisted": index_file.exists(),
        "index_file": str(index_file),
    }
