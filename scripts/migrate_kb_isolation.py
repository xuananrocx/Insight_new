"""一次性迁移脚本：修复多 KB 向量隔离 bug。

背景：
    旧版 _resolve_collection 把所有非 feedback collection 都映射到 main_collection，
    导致不同 KB 的 chunks 全部混在 'knowledge_base' 这一个 collection 里。
    BM25 索引同理，也是全局共享的。

修复后：
    每个 KB 拥有独立的 collection（按 collection_name 隔离）。
    BM25 query 支持按 kb_id 过滤。

迁移步骤：
    1. 备份当前 metadata.db
    2. 清空所有 Chroma collection + BM25 索引
    3. 重置所有文件的 chunk_ids_json / status
    4. 对每个 KB 重新 ingest 它的所有源文件（使用修复后的隔离逻辑）

使用：
    ./.venv/bin/python scripts/migrate_kb_isolation.py
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

# 让脚本能 import src
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core import vector_store
from src.core.config import settings
from src.db import metadata_db
from src.knowledge import ingestion
from src.qa import bm25_index


def main() -> None:
    metadata_db.init_db()

    # 1. 备份
    db_path = settings.get_path("metadata_db")
    if db_path.exists():
        backup = db_path.with_suffix(f".db.backup-{int(time.time())}")
        shutil.copy2(db_path, backup)
        print(f"[1/5] 已备份 metadata.db → {backup.name}")
    else:
        print("[1/5] metadata.db 不存在，跳过备份")

    # 2. 清空 Chroma 所有 collection
    client = vector_store.get_chroma_client()
    collections_before = [c.name for c in client.list_collections()]
    print(f"[2/5] 当前 Chroma collections: {collections_before}")
    for name in collections_before:
        client.delete_collection(name)
        print(f"   - 已删除 collection: {name}")

    # 3. 清空 BM25 索引
    bm25_index.clear()
    print("[3/5] 已清空 BM25 索引")

    # 4. 重置所有文件状态为 pending（清空 chunk_ids）
    all_files = metadata_db.list_files(limit=100000)
    done_count = 0
    for f in all_files:
        if f.get("status") == "done":
            metadata_db.reset_file_to_pending(f["id"])
            done_count += 1
    print(f"[4/5] 已重置 {done_count} 个文件状态为 pending")

    # 5. 对每个 KB 重新 ingest 它的文件
    kbs = metadata_db.list_kbs()
    print(f"[5/5] 开始重新入库 {len(kbs)} 个 KB...")
    for kb in kbs:
        kb_id = kb["id"]
        kb_name = kb["name"]
        files = metadata_db.list_files(kb_id=kb_id, limit=100000)
        todo = [f for f in files if f.get("status") != "done"]
        print(f"   [{kb_id}] {kb_name}: 待入库 {len(todo)} 个文件")
        for f in todo:
            abs_path = Path(f["absolute_path"])
            if not abs_path.exists():
                print(f"     - 跳过（文件不存在）: {f['relative_path']}")
                continue
            try:
                result = ingestion.ingest_single_file(abs_path, kb_id=kb_id)
                added = result.get("added", 0)
                updated = result.get("updated", 0)
                failed = result.get("failed", 0)
                print(
                    f"     - {f['relative_path']}: "
                    f"+{added} ~{updated} ✗{failed}"
                )
            except Exception as e:
                print(f"     - 失败: {f['relative_path']} - {e}")

    # 完成
    print("\n=== 迁移完成 ===")
    print("Chroma collections:")
    for c in client.list_collections():
        print(f"  - {c.name}: {c.count()} chunks")
    print(f"BM25 索引: {bm25_index.stats()['chunk_count']} chunks")


if __name__ == "__main__":
    main()
