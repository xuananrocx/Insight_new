"""src/qa/bm25_index.py 关键路径测试。"""
import json

import pytest

# rank_bm25 是可选依赖，缺失时跳过 BM25 相关测试
pytest.importorskip("rank_bm25")


def _make_chunk(cid: str, text: str) -> dict:
    return {"id": cid, "text": text, "metadata": {}}


def test_bm25_add_query_remove():
    """基本 add → query → remove 流程。"""
    from src.qa import bm25_index
    bm25_index._state = None  # reset
    bm25_index.clear()
    bm25_index.add_chunks([_make_chunk("c1", "AMD ERR-001 行情序列号")])
    hits = bm25_index.query("ERR-001")
    assert len(hits) >= 1
    assert hits[0]["id"] == "c1"
    bm25_index.remove_chunks(["c1"])
    assert bm25_index.query("ERR-001") == []


def test_bm25_query_empty_after_clear():
    from src.qa import bm25_index
    bm25_index.clear()
    bm25_index.add_chunks([_make_chunk("c1", "test content")])
    bm25_index.clear()
    assert bm25_index.query("test") == []


def test_bm25_persists_to_json_not_pickle():
    """持久化文件应是 JSON（S-5 修复后）。"""
    from src.qa import bm25_index
    bm25_index.clear()
    bm25_index.add_chunks([_make_chunk("c1", "persist test")])
    # 文件应是 .json
    idx_file = bm25_index._get_index_file()
    assert idx_file.suffix == ".json"
    # 内容应是合法 JSON
    with open(idx_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "chunk_ids" in data
    assert "c1" in data["chunk_ids"]


def test_bm25_add_chunks_no_duplicates():
    """同 id 重复 add 应替换，不应重复存储。"""
    from src.qa import bm25_index
    bm25_index.clear()
    bm25_index.add_chunks([_make_chunk("c1", "old text")])
    bm25_index.add_chunks([_make_chunk("c1", "new text")])
    state = bm25_index._state_load()
    assert state["chunk_ids"].count("c1") == 1
    assert state["chunks"][0]["text"] == "new text"
