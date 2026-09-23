"""src/qa/bm25_index.py 关键路径测试。"""
import json

import pytest

@pytest.fixture(autouse=True)
def initialized():
    from src.db import metadata_db
    metadata_db.init_db()


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


def test_keyword_index_persists_to_sqlite():
    from src.qa import bm25_index, lexical_store
    bm25_index.clear()
    bm25_index.add_chunks([_make_chunk('c1', 'persist test')])
    with lexical_store.connection() as conn:
        assert conn.execute('SELECT id FROM chunks').fetchone()[0] == 'c1'
    assert bm25_index.stats()['backend'] == 'sqlite_fts5'


def test_bm25_add_chunks_no_duplicates():
    """同 id 重复 add 应替换，不应重复存储。"""
    from src.qa import bm25_index
    bm25_index.clear()
    bm25_index.add_chunks([_make_chunk("c1", "old text")])
    bm25_index.add_chunks([_make_chunk("c1", "new text")])
    assert bm25_index.stats()["chunk_count"] == 1
    assert bm25_index.query("new")[0]["text"] == "new text"


def test_legacy_migration_runs_once_and_preserves_updates(tmp_path, monkeypatch):
    from src.qa import bm25_index, lexical_store
    monkeypatch.setattr(lexical_store, 'path', lambda: tmp_path / 'keyword-index.sqlite')
    legacy = tmp_path / 'bm25_index.json'
    original = json.dumps({'chunks': [{'id': 'legacy', 'text': 'original token'}]})
    legacy.write_text(original, encoding='utf-8')
    assert bm25_index.query('original')[0]['id'] == 'legacy'
    bm25_index.add_chunks([_make_chunk('legacy', 'replacement token')])
    assert bm25_index.query('original') == []
    assert bm25_index.query('replacement')[0]['id'] == 'legacy'
    bm25_index.remove_chunks(['legacy'])
    assert bm25_index.stats()['chunk_count'] == 0
    assert legacy.read_text(encoding='utf-8') == original
