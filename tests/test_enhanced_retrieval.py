"""Regression cases for evidence recall and section-safe context expansion."""
from types import SimpleNamespace

import pytest

from src.core import vector_store
from src.core.config import settings
from src.qa import bm25_index, rag, retrieval
from src.qa.trace import PipelineCancelled, TraceCollector


@pytest.fixture(autouse=True)
def initialized_index_db():
    from src.db import metadata_db
    metadata_db.init_db()


def chunk(cid, text, **meta):
    return {"id": cid, "text": text, "source_path": "guide.pdf", "content_hash": "hash",
            "section_index": 2, "chunk_index": 1, "score": 0.8, **meta}


def test_keyword_match_and_persistent_reload():
    bm25_index.clear()
    bm25_index.add_chunks([chunk("a", "QueryMDTick"), chunk("b", "OtherAPI")])
    hits = bm25_index.query_enhanced("QueryMDTick")
    assert [h["id"] for h in hits] == ["a"]
    assert hits[0]["bm25_score"] > 0
    bm25_index._state = None
    assert [h["id"] for h in bm25_index.query_enhanced("QueryMDTick")] == ["a"]


def test_common_keyword_and_kb_isolation():
    bm25_index.clear()
    bm25_index.add_chunks([
        {"id": "a", "text": "QueryMDTick", "metadata": {"kb_id": "default"}},
        {"id": "b", "text": "QueryMDTick", "metadata": {"kb_id": "other"}},
        {"id": "s", "text": "QueryMDTick", "metadata": {"chunk_type": "summary"}},
    ])
    hits = bm25_index.query_enhanced("QueryMDTick")
    assert [h["id"] for h in hits] == ["a"]
    assert hits[0]["bm25_score"] > 0


def test_exact_identifier_prefers_full_error_code():
    bm25_index.clear()
    bm25_index.add_chunks([chunk("wrong", "ERR-002 重试"), chunk("right", "ERR-001 断开")])
    assert bm25_index.query_enhanced("ERR-001 怎么处理")[0]["id"] == "right"


def test_context_queries_metadata_not_global_chunk_id(monkeypatch):
    recorded = {}

    def get(**kwargs):
        recorded.update(kwargs)
        return {"ids": ["global-44", "global-43"], "documents": ["正文", "前提"],
                "metadatas": [{"chunk_index": 1}, {"chunk_index": 0}]}

    monkeypatch.setattr(vector_store, "_resolve_collection", lambda name: SimpleNamespace(get=get))
    result = vector_store.get_context_chunks(chunk("global-44", "正文"), "kb_test")
    assert [r["id"] for r in result] == ["global-43", "global-44"]
    assert {"section_index": 2} in recorded["where"]["$and"]
    assert {"content_hash": "hash"} in recorded["where"]["$and"]
    assert recorded["limit"] == 5


def test_context_never_crosses_section_or_version(monkeypatch):
    center = chunk("global-44", "步骤", chunk_index=1)
    rows = [center, chunk("global-43", "前置条件", chunk_index=0),
            chunk("bad-section", "错误章节", chunk_index=2, section_index=0),
            chunk("bad-version", "旧版本", chunk_index=2, content_hash="old")]
    monkeypatch.setattr(vector_store, "get_context_chunks", lambda *a, **k: rows)
    result = retrieval.expand_context([center], "kb_test", TraceCollector())
    assert result[0]["text"] == "前置条件\n\n步骤"
    assert result[0]["merged_chunks"] == 1


def test_missing_section_is_not_guessed(monkeypatch):
    def forbidden(*a, **k):
        pytest.fail("incomplete metadata must not be used to guess neighbors")
    monkeypatch.setattr(vector_store, "_resolve_collection", forbidden)
    hit = chunk("a", "正文")
    del hit["section_index"]
    assert vector_store.get_context_chunks(hit, "kb_test") == []


def test_context_lookup_against_real_chroma(monkeypatch, tmp_path):
    import chromadb
    from chromadb.config import Settings
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"),
                                     settings=Settings(anonymized_telemetry=False))
    collection = client.create_collection("context-test", embedding_function=None)
    rows = [chunk("global-43", "前提", chunk_index=0), chunk("global-44", "步骤"),
            chunk("global-45", "验证", chunk_index=2),
            chunk("other-section", "其他章节", section_index=1)]
    collection.add(ids=[r["id"] for r in rows], documents=[r["text"] for r in rows],
                   metadatas=[{k: v for k, v in r.items() if k not in ("id", "text")} for r in rows],
                   embeddings=[[1.0, 0.0] for r in rows])
    monkeypatch.setattr(vector_store, "_resolve_collection", lambda name: collection)
    assert [r["id"] for r in vector_store.get_context_chunks(rows[1], "context-test")] == [
        "global-43", "global-44", "global-45"]


def test_diversity_removes_duplicate_and_keeps_missing_aspect():
    hits = [chunk("a", "接口登录步骤", source_path="a"),
            chunk("b", "接口登录步骤", source_path="b"),
            chunk("c", "失败错误处理", source_path="c")]
    result = retrieval.select_evidence(hits, 2, ["登录步骤", "错误处理"])
    assert {h["id"] for h in result} == {"a", "c"}


@pytest.mark.parametrize("deep", [False, True])
def test_stale_lexical_entries_excluded_and_raw_evidence_preserved(monkeypatch, deep):
    monkeypatch.setattr(settings, "_config", {"qa": {"rerank": {"enabled": True}}})
    monkeypatch.setattr(retrieval.metadata_db, "get_kb", lambda _: {"collection_name": "scoped"})
    monkeypatch.setattr(retrieval.llm_client, "get_client", lambda: SimpleNamespace(
        embed=lambda qs: ([[0.1, 0.2] for _ in qs], "fake-embedding")))
    called = []

    def query(vector, **kwargs):
        called.append(kwargs)
        return [chunk("a", "原文甲", title="标题甲"), chunk("s", "摘要", chunk_type="summary")]

    monkeypatch.setattr(vector_store, "query_by_embedding", query)
    monkeypatch.setattr(bm25_index, "query_enhanced", lambda *a: [chunk("gone", "已删除"), chunk("b", "陈旧内容")])
    monkeypatch.setattr(vector_store, "get_chunks_by_ids", lambda ids, **k: {"b": chunk("b", "实时原文乙")})
    monkeypatch.setattr(vector_store, "get_context_chunks", lambda *a: [])
    monkeypatch.setattr(rag, "_expand_with_concepts", lambda q, h, *a: (h, []))

    def rerank(q, hits, **kwargs):
        assert deep
        assert any("标题甲" in h["text"] for h in hits)
        return hits

    monkeypatch.setattr(retrieval.reranker, "rerank", rerank)
    hits, _ = retrieval.search("问题", 3, "target-kb", TraceCollector(), deep=deep)
    assert {h["id"] for h in hits} == {"a", "b"}
    assert {h["text"] for h in hits} == {"原文甲", "实时原文乙"}
    assert called[0]["collection_name"] == "scoped"
    assert called[0]["k"] > 3


def test_cancellation_is_not_swallowed_by_trace_listener():
    trace = TraceCollector()
    stopped = False

    def stop(_):
        nonlocal stopped
        stopped = True

    def check():
        if stopped:
            raise PipelineCancelled()

    trace.on_stage_complete(stop)
    trace.cancel_check = check
    with pytest.raises(PipelineCancelled):
        with trace.stage("first", "第一步"):
            pass
    assert len(trace.to_list()) == 1
