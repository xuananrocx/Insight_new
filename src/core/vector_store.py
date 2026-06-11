"""向量存储 - Chroma 的唯一访问点。

重要架构约束：
    Chroma 底层基于 SQLite，不支持多进程并发写入同一目录。
    因此 Chroma client 必须只在 FastAPI 主进程内创建，
    Streamlit 等其他进程通过 HTTP API 调用，绝不直接访问。

设计：
- get_collection(): 主知识库 collection
- upsert_chunks(chunks): 用自定义 chunk_id（可控）批量写入
- delete_chunks(ids): 按 id 删除
- query(question_vec, k): 相似度检索
"""
from __future__ import annotations

import hashlib
import uuid
from functools import lru_cache
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from src.core.config import settings


COLLECTION_NAME = "knowledge_base"
FEEDBACK_COLLECTION_NAME = "feedback_qa"  # 审批通过的人工问答对


@lru_cache(maxsize=1)
def get_chroma_client() -> chromadb.api.ClientAPI:
    """返回 Chroma client（单例）。"""
    path = settings.get_path("vector_db")
    path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(path),
        settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False),
    )
    return client


def get_main_collection() -> chromadb.api.Collection:
    """主知识库 collection。"""
    client = get_chroma_client()
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"description": "AMD AI Assistant 主知识库"},
    )


def get_feedback_collection() -> chromadb.api.Collection:
    """审批通过的人工问答 collection（高分优先）。"""
    client = get_chroma_client()
    return client.get_or_create_collection(
        name=FEEDBACK_COLLECTION_NAME,
        metadata={"description": "审批通过的人工问答"},
    )


def make_chunk_id(source_path: str, content_hash: str, chunk_index: int) -> str:
    """生成稳定的 chunk_id：基于来源路径 + 文件 hash + chunk 序号。

    这样同一文件重新入库时，chunk_id 保持稳定，可以直接 upsert。
    """
    raw = f"{source_path}|{content_hash}|{chunk_index}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def upsert_chunks(
    chunks: list[dict[str, Any]],
    collection_name: str = COLLECTION_NAME,
) -> list[str]:
    """批量写入 chunks。

    参数：
        chunks: [{"id": "...", "text": "...", "embedding": [...], "metadata": {...}}, ...]
    返回：
        写入的 chunk_id 列表
    """
    if not chunks:
        return []
    client = get_chroma_client()
    collection = client.get_or_create_collection(name=collection_name)
    ids = [c["id"] for c in chunks]
    texts = [c["text"] for c in chunks]
    embeddings = [c["embedding"] for c in chunks]
    metadatas = [c["metadata"] for c in chunks]
    # Chroma metadata 值必须为 str/int/float/bool（不能是 None）
    clean_meta = [_sanitize_meta(m) for m in metadatas]
    collection.upsert(
        ids=ids,
        documents=texts,
        embeddings=embeddings,
        metadatas=clean_meta,
    )
    return ids


def delete_chunks(ids: list[str], collection_name: str = COLLECTION_NAME) -> None:
    """按 id 删除 chunks。"""
    if not ids:
        return
    client = get_chroma_client()
    try:
        collection = client.get_collection(name=collection_name)
    except Exception:
        return
    collection.delete(ids=ids)


def query_by_embedding(
    embedding: list[float],
    k: int = 5,
    where: dict | None = None,
    collection_name: str = COLLECTION_NAME,
) -> list[dict[str, Any]]:
    """根据 embedding 检索最相似的 chunks。"""
    client = get_chroma_client()
    try:
        collection = client.get_collection(name=collection_name)
    except Exception:
        return []
    res = collection.query(
        query_embeddings=[embedding],
        n_results=k,
        where=where,
    )
    return _format_query_result(res)


def query_collections(
    embedding: list[float],
    k: int = 5,
    collections: list[str] | None = None,
) -> list[dict[str, Any]]:
    """跨多个 collection 检索（如主知识库 + feedback 库），合并排序。

    返回最多 k 条结果。
    """
    if collections is None:
        collections = [COLLECTION_NAME, FEEDBACK_COLLECTION_NAME]
    all_hits: list[dict[str, Any]] = []
    for cn in collections:
        hits = query_by_embedding(embedding, k=k, collection_name=cn)
        all_hits.extend(hits)
    # 按距离升序
    all_hits.sort(key=lambda x: x.get("distance", 1.0))
    # 去重：同一文本只保留一次
    seen = set()
    out: list[dict[str, Any]] = []
    for h in all_hits:
        key = (h.get("source_path", ""), h.get("text", "")[:100])
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= k:
            break
    return out


def _format_query_result(res: Any) -> list[dict[str, Any]]:
    """Chroma 返回值 → 简化结构。"""
    out: list[dict[str, Any]] = []
    if not res or not res.get("ids"):
        return out
    ids_batch = res["ids"][0]
    docs_batch = res["documents"][0]
    metas_batch = res["metadatas"][0]
    dists_batch = res["distances"][0] if res.get("distances") else [0.0] * len(ids_batch)
    for i, _id in enumerate(ids_batch):
        meta = metas_batch[i] if i < len(metas_batch) else {}
        out.append({
            "id": _id,
            "text": docs_batch[i] if i < len(docs_batch) else "",
            "distance": float(dists_batch[i]) if i < len(dists_batch) else 0.0,
            "score": 1.0 - float(dists_batch[i]) if i < len(dists_batch) else 0.0,
            **meta,
        })
    return out


def _sanitize_meta(m: dict[str, Any]) -> dict[str, Any]:
    """清理 metadata，使其符合 Chroma 要求（值必须是基础类型）。"""
    out: dict[str, Any] = {}
    for k, v in m.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        else:
            out[k] = str(v)
    return out


def count(collection_name: str = COLLECTION_NAME) -> int:
    """返回 collection 内的 chunk 数。"""
    try:
        client = get_chroma_client()
        collection = client.get_collection(name=collection_name)
        return collection.count()
    except Exception:
        return 0


def health_check() -> dict:
    """健康检查。"""
    try:
        client = get_chroma_client()
        heartbeat = client.heartbeat()
        collection_names = [c.name for c in client.list_collections()]
        counts = {name: count(name) for name in collection_names}
        return {
            "status": "ok",
            "heartbeat": heartbeat,
            "collections": collection_names,
            "counts": counts,
            "path": str(settings.get_path("vector_db")),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
