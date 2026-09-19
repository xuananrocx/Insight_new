"""Regression for a successful recall followed by slow CPU reranking."""
import asyncio
import threading

import pytest

from src.core.config import settings
from src.qa import deep_ai, retrieval
from src.qa.trace import TraceCollector


def hits(count):
    return [{"id": str(i), "text": f"配置证据 {i}", "source_path": f"doc-{i}", "score": 0.8}
            for i in range(count)]


def test_rerank_limits_candidates_and_batch_size(monkeypatch):
    batches = []

    def rerank(question, batch, **kwargs):
        batches.append(len(batch))
        return [{**h, "rerank_score": 0.9} for h in batch]

    monkeypatch.setattr(retrieval.reranker, "rerank", rerank)
    trace = TraceCollector()
    result = retrieval._bounded_rerank("ama.json", hits(80), {}, trace)
    assert len(result) == 24
    assert batches == [4] * 6
    assert all(h["text"].startswith("配置证据") for h in result)


def test_budget_expiry_preserves_unscored_evidence(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(retrieval.time, "monotonic", lambda: now[0])

    def rerank(question, batch, **kwargs):
        now[0] = 21.0
        return [{**h, "rerank_score": 0.9} for h in batch]

    monkeypatch.setattr(retrieval.reranker, "rerank", rerank)
    trace = TraceCollector()
    result = retrieval._bounded_rerank("ama.json", hits(12), {}, trace)
    assert len(result) == 12
    assert sum("rerank_score" in h for h in result) == 4
    assert trace.to_list()[-1]["status"] == "partial"


def test_duplicate_code_and_distinct_settings_are_not_confused():
    common = '"Login": {"User": "example", "Password": "example"},\n' * 35
    documents = [
        {"id": "a", "text": common + '"Market": "SSE"', "source_path": "guide"},
        {"id": "duplicate", "text": common + '"Market": "SSE"', "source_path": "guide"},
        {"id": "different", "text": '"Subscribe": {"SecurityCode": "600570"}', "source_path": "guide"},
    ]
    assert [h["id"] for h in retrieval.select_evidence(documents, 3)] == ["a", "different"]


@pytest.mark.asyncio
async def test_timeout_during_retrieval_keeps_checkpointed_sources(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(settings, "_config", {"qa": {}})
    original_number = deep_ai._number
    monkeypatch.setattr(deep_ai, "_number", lambda cfg, key, *a:
                        0.05 if key == "total_timeout_seconds" else original_number(cfg, key, *a))
    monkeypatch.setattr(deep_ai.llm_client, "get_client", object)

    async def plan(*a, **k):
        return {}

    def search(question, k, kb, trace, **kwargs):
        kwargs["on_evidence"](hits(3), "embedding")
        release.wait(2)
        trace.cancel_check()
        return hits(3), "embedding"

    monkeypatch.setattr(deep_ai, "_json_call", plan)
    monkeypatch.setattr(retrieval, "search", search)
    try:
        events = [e async for e in deep_ai.ask_stream("ama.json", None, "default",
                  top_k=10, session_id=None, turn_id=None)]
        result = events[-1]["data"]
        assert result["verification"] == "timeout"
        assert len(result["sources"]) == 3
        assert "未找到" not in result["answer"]
        assert "配置证据" in result["answer"]
        assert any(t["stage"] == "timeout" for t in result["trace"])
    finally:
        release.set()
        await asyncio.sleep(0)


def test_timeout_without_sources_does_not_claim_no_documents():
    answer = deep_ai.evidence_only([], "深度分析超时。")
    assert "超时" in answer
    assert "这不代表知识库中没有相关资料" in answer
    assert "未找到足以支持" not in answer
