"""一次性回填：把各 KB collection 的现有 chunks 重建进 BM25 索引。

背景：requirements.txt 曾漏声明 jieba/rank-bm25，ingest 时 add_chunks 静默失败，
BM25 索引从未生成（bm25_index.json 不存在）。装齐依赖后用本脚本回填，
无需重新 embedding。

用法（Insight 根目录，.venv python，不设 AMD_DATA_DIR）：
    .venv/Scripts/python.exe scripts/backfill_bm25.py

幂等：add_chunks 对已存在 id 做替换，重复跑无副作用。
每个 KB 的 collection 名存在 kbs.collection_name（不是 kb id 本身）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core import vector_store
from src.db import metadata_db
from src.qa import bm25_index

PAGE = 1000


def backfill_collection(collection_name: str) -> int:
    collection = vector_store._resolve_collection(collection_name)
    total = collection.count()
    added = 0
    offset = 0
    while True:
        res = collection.get(include=["documents", "metadatas"], limit=PAGE, offset=offset)
        ids = res.get("ids") or []
        if not ids:
            break
        docs = res.get("documents") or []
        metas = res.get("metadatas") or []
        payloads = []
        for i, cid in enumerate(ids):
            payloads.append({
                "id": str(cid),
                "text": docs[i] if i < len(docs) else "",
                "metadata": (metas[i] if i < len(metas) else {}) or {},
            })
        bm25_index.add_chunks(payloads)
        added += len(payloads)
        if len(ids) < PAGE:
            break
        offset += PAGE
    print(f"[backfill] {collection_name}: {added}/{total}")
    return added


def main() -> int:
    kbs = metadata_db.list_kbs()
    print(f"[backfill] 共 {len(kbs)} 个 KB")
    total = 0
    for kb in kbs:
        total += backfill_collection(kb["collection_name"])

    after = bm25_index.stats()
    print(f"[backfill] 完成: BM25 chunk_count={after['chunk_count']} file={after['index_file']}")

    state = bm25_index._state_load()
    from collections import Counter
    dist = Counter((c.get("kb_id") or "default") for c in state["chunks"])
    print(f"[backfill] kb_id 分布: {dict(dist)}")

    hits = bm25_index.query("QueryMDTick 查不到数据", top_k=5, kb_id="kb_c3c76c19539a482984476a5b9f590a35")
    print(f"[backfill] 试查（AMA知识库）命中 {len(hits)} 条")
    if not hits:
        print("[backfill] WARN: 试查 0 命中，请人工检查")
        return 1
    print("[backfill] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
