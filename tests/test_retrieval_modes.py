"""三档检索模式测试：basic/deep 不调 LLM、deep 合并/高亮/归一化、会话级 mode 持久化。"""
import asyncio

import pytest

from src.db import metadata_db
from src.qa import rag
from src.qa.trace import TraceCollector


# ===== 纯函数 =====


def test_join_with_overlap_dedupes():
    assert rag._join_with_overlap("前面内容重叠区", "重叠区后面内容") == "前面内容重叠区后面内容"
    assert rag._join_with_overlap("abc", "def") == "abcdef"
    assert rag._join_with_overlap("", "def") == "def"
    assert rag._join_with_overlap("abc", "") == "abc"


def test_neighbor_indexes_bounds():
    # 章节首 chunk 无前邻
    assert rag._neighbor_indexes({"chunk_index": 0, "chunk_total_in_section": 3}) == (None, 1)
    # 章节尾 chunk 无后邻
    assert rag._neighbor_indexes({"chunk_index": 2, "chunk_total_in_section": 3}) == (1, None)
    # 无 total 元数据时默认有后邻
    assert rag._neighbor_indexes({"chunk_index": 1}) == (0, 2)
    # 无 chunk_index（feedback Q&A 等）双 None
    assert rag._neighbor_indexes({"title": "x"}) == (None, None)


def test_normalize_score_pct():
    hits = [{"score": 0.9}, {"score": 0.45}, {"score": 0.0}]
    rag._normalize_score_pct(hits)
    assert hits[0]["score_pct"] == 100
    assert hits[1]["score_pct"] == 50
    assert hits[2]["score_pct"] == 1

    # 全 0 分：按排名降序兜底
    zero = [{"score": 0.0}, {"score": 0.0}]
    rag._normalize_score_pct(zero)
    assert zero[0]["score_pct"] >= zero[1]["score_pct"] >= 1


def test_extract_highlight_terms_filters_stopwords():
    terms = rag._extract_highlight_terms("QueryMDTick 查不到数据怎么排查")
    assert "QueryMDTick" in terms
    assert "怎么" not in terms


def test_hits_to_search_results_fields():
    hits = [{
        "source_name": "guide.pdf", "title": "指南", "section_label": "§5",
        "file_type": "pdf", "text": "内容", "score": 0.9, "score_pct": 100,
        "merged_chunks": 2,
    }]
    out = rag._hits_to_search_results(hits)
    assert out[0]["content"] == "内容"
    assert out[0]["score_pct"] == 100
    assert out[0]["merged_chunks"] == 2


# ===== deep 相邻合并 =====


def _hit(idx: int, total: int = 3, text: str = "中心片段", hid: str | None = None) -> dict:
    return {
        "id": hid or f"center-{idx}",
        "text": text,
        "source_path": "C:/docs/guide.pdf",
        "content_hash": "abc123",
        "chunk_index": idx,
        "chunk_total_in_section": total,
        "source_name": "guide.pdf",
        "title": "指南",
        "section_label": "§5",
        "file_type": "pdf",
        "score": 0.9,
    }


class _FakeVS:
    """替身 vector_store：按 id 返回预置 chunk。"""
    def __init__(self, store: dict):
        self.store = store
        self.calls: list[list[str]] = []

    def make_chunk_id(self, source_path: str, content_hash: str, chunk_index: int) -> str:
        return f"{source_path}|{content_hash}|{chunk_index}"

    def get_chunks_by_ids(self, ids, collection_name=None):
        self.calls.append(list(ids))
        return {i: self.store[i] for i in ids if i in self.store}


def test_merge_neighbor_chunks_merges_both_sides(monkeypatch):
    fake = _FakeVS({
        "C:/docs/guide.pdf|abc123|0": {"text": "前一段落", "id": "n0"},
        "C:/docs/guide.pdf|abc123|2": {"text": "后一段落", "id": "n2"},
    })
    monkeypatch.setattr(rag, "vector_store", fake)

    hits = [_hit(1, text="中心片段")]
    merged = rag._merge_neighbor_chunks(hits, "kb_x", TraceCollector())

    assert merged[0]["merged_chunks"] == 2
    assert "前一段落" in merged[0]["text"]
    assert "后一段落" in merged[0]["text"]
    assert "中心片段" in merged[0]["text"]


def test_merge_neighbor_chunks_skips_missing_and_centers(monkeypatch):
    # 两个命中互为相邻 chunk（同 source/hash，index 0 和 1）→ 不互取、不合并
    fake = _FakeVS({})
    monkeypatch.setattr(rag, "vector_store", fake)

    center_hit = _hit(0, total=2, hid="C:/docs/guide.pdf|abc123|0")
    neighbor_is_center = _hit(1, total=2, text="相邻命中", hid="C:/docs/guide.pdf|abc123|1")
    merged = rag._merge_neighbor_chunks([center_hit, neighbor_is_center], "kb_x", TraceCollector())

    assert all("merged_chunks" not in h for h in merged)
    # 相邻 id 已是命中本身，不应发起取回
    wanted = {i for call in fake.calls for i in call}
    assert "C:/docs/guide.pdf|abc123|1" not in wanted


def test_merge_neighbor_chunks_skips_no_hash(monkeypatch):
    fake = _FakeVS({})
    monkeypatch.setattr(rag, "vector_store", fake)
    h = _hit(1)
    h.pop("content_hash")
    merged = rag._merge_neighbor_chunks([h], "kb_x", TraceCollector())
    assert "merged_chunks" not in merged[0]


# ===== ask_stream 三档行为 =====


class _FakeLLMClient:
    """LLM 客户端替身：chat/chat_stream 一旦被调用即失败（basic/deep 不允许碰 LLM）。"""
    def is_embedding_warm(self):
        return True

    def chat(self, *a, **k):
        raise AssertionError("basic/deep 模式不应调用 LLM chat")

    async def chat_stream(self, *a, **k):
        raise AssertionError("basic/deep 模式不应调用 LLM chat_stream")
        yield  # pragma: no cover


def _fake_hits() -> list[dict]:
    return [
        {
            "id": "h1", "text": "片段一：QueryMDTick 用法", "source_path": "C:/a.pdf",
            "source_name": "a.pdf", "title": "a", "section_label": "§1",
            "file_type": "pdf", "score": 0.9,
        },
        {
            "id": "h2", "text": "片段二", "source_path": "C:/b.pdf",
            "source_name": "b.pdf", "title": "b", "section_label": "§2",
            "file_type": "pdf", "score": 0.5,
        },
    ]


def _patch_pipeline(monkeypatch, hits):
    def fake_pipeline(question, top_k, history, trace, system_override, kb_scope, strategy):
        assert strategy in ("basic", "deep", "agentic")
        fake_pipeline.last_strategy = strategy
        return None, hits, "local-bge"
    fake_pipeline.last_strategy = None
    monkeypatch.setattr(rag, "_run_pipeline", fake_pipeline)
    return fake_pipeline


def _collect(mode: str) -> list[dict]:
    return asyncio.run(_collect_async(mode))


async def _collect_async(mode: str) -> list[dict]:
    events = []
    async for evt in rag.ask_stream("QueryMDTick 怎么用", kb_scope="default", mode=mode):
        events.append(evt)
    return events


@pytest.mark.parametrize("mode", ["basic", "deep"])
def test_search_modes_never_call_llm(monkeypatch, mode):
    monkeypatch.setattr(rag.llm_client, "get_client", lambda: _FakeLLMClient())
    pipeline = _patch_pipeline(monkeypatch, _fake_hits())

    events = _collect(mode)

    assert pipeline.last_strategy == mode
    types = [e["type"] for e in events]
    assert "results" in types and "token" not in types

    results = next(e for e in events if e["type"] == "results")["data"]
    assert results["mode"] == mode
    assert results["highlight_terms"]
    assert len(results["hits"]) == 2
    assert results["hits"][0]["score_pct"] == 100  # 最高命中归一化基准

    done = next(e for e in events if e["type"] == "done")["data"]
    assert done["answer"] == ""
    assert done["mode"] == mode
    assert done["used_provider"] == "local-bge"


def test_ai_mode_uses_isolated_pipeline_and_streams(monkeypatch):
    class _StreamingClient(_FakeLLMClient):
        async def chat_stream(self, messages, **k):
            yield "你好", "fake-provider"

    monkeypatch.setattr(rag.llm_client, "get_client", lambda: _StreamingClient())
    pipeline = _patch_pipeline(monkeypatch, _fake_hits())

    from src.qa import enhanced_ai
    monkeypatch.setattr(enhanced_ai, 'prepare', lambda *a, **k: ([{'role': 'user', 'content': 'question'}], _fake_hits(), 'local-bge'))
    events = _collect("weird-mode")  # 非法值回落 ai

    assert pipeline.last_strategy is None
    types = [e["type"] for e in events]
    assert "results" not in types
    assert "sources" in types and "token" in types
    done = next(e for e in events if e["type"] == "done")["data"]
    assert done["answer"] == "你好"
    assert done["mode"] == "ai"


def test_search_mode_empty_hits_returns_empty_results(monkeypatch):
    monkeypatch.setattr(rag.llm_client, "get_client", lambda: _FakeLLMClient())
    _patch_pipeline(monkeypatch, [])

    events = _collect("basic")
    results = next(e for e in events if e["type"] == "results")["data"]
    assert results["hits"] == []
    done = next(e for e in events if e["type"] == "done")["data"]
    assert done["answer"] == ""


# ===== 会话级 mode 持久化（schema v13） =====


def test_session_retrieval_mode_crud():
    metadata_db.init_db()
    sid = "test-mode-sid"
    metadata_db.create_session(sid, "t", 1000, 1000, retrieval_mode="deep")
    try:
        assert metadata_db.get_session(sid)["retrieval_mode"] == "deep"
        assert metadata_db.list_sessions()[0]["retrieval_mode"] == "deep"

        metadata_db.update_session(sid, retrieval_mode="basic")
        assert metadata_db.get_session(sid)["retrieval_mode"] == "basic"

        with pytest.raises(ValueError):
            metadata_db.update_session(sid, retrieval_mode="nope")

        # turn 携带消息形态
        assert metadata_db.add_turn(
            sid, "t1", None, "q", "", "[]", "[]", None, False, None, 1000, mode="deep",
        )
        s = metadata_db.get_session(sid)
        assert s["turns"][0]["mode"] == "deep"
        assert metadata_db.get_turn(sid, "t1")["mode"] == "deep"

        # 不传 mode 默认 ai
        metadata_db.add_turn(sid, "t2", None, "q2", "a", "[]", "[]", None, False, None, 1001)
        assert metadata_db.get_turn(sid, "t2")["mode"] == "ai"
    finally:
        metadata_db.delete_session(sid)


def test_fresh_db_has_v13_columns():
    metadata_db.init_db()
    with metadata_db.get_cursor() as cur:
        s_cols = [r[1] for r in cur.execute("PRAGMA table_info(sessions)").fetchall()]
        t_cols = [r[1] for r in cur.execute("PRAGMA table_info(turns)").fetchall()]
    assert "retrieval_mode" in s_cols
    assert "mode" in t_cols


def test_basic_strategy_skips_rerank(monkeypatch):
    """basic 档跳过 cross-encoder 重排（CPU 重排 20 对候选约 30s），直接截断到 top_k。"""
    from src.core import llm_client as lc
    from src.core import vector_store as vs
    from src.core.config import settings
    from src.db import metadata_db as mdb
    from src.qa import rag
    from src.qa import bm25_index as bi
    from src.qa import reranker as rr
    from src.qa.trace import TraceCollector

    monkeypatch.setattr(settings, "_config", {"qa": {"top_k": 3, "similarity_threshold": 0.0}})
    monkeypatch.setattr(lc, "get_client", lambda: type("C", (), {"embed": staticmethod(lambda t: ([[0.1, 0.2, 0.3]], "fake-provider"))})())
    monkeypatch.setattr(mdb, "get_kb", lambda kb_id: {"id": kb_id, "collection_name": "kb_test"})
    monkeypatch.setattr(vs, "query_by_embedding", lambda *a, **k: [{"id": f"c{i}", "text": f"内容{i}", "score": 0.9 - i * 0.1} for i in range(10)])
    monkeypatch.setattr(bi, "query_enhanced", lambda *a, **k: [])

    def boom(*a, **k):
        raise AssertionError("basic 档不应调用 rerank")

    monkeypatch.setattr(rr, "rerank", boom)

    trace = TraceCollector()
    messages, hits, provider = rag._run_pipeline(
        "测试问题", top_k=3, history=None, trace=trace, kb_scope="kb_test", strategy="basic",
    )
    assert provider == "fake-provider"
    assert len(hits) == 3  # 未重排，按召回顺序截断到 top_k
    stage = next(s for s in trace.to_list() if s["stage"] == "rerank")
    assert stage["status"] == "skipped"
    assert "basic" in stage.get("notes", "")


def _fake_onnx(logit_fn):
    import numpy as np

    class FakeTok:
        def __call__(self, queries, texts, **kw):
            n = len(texts)
            return {
                "input_ids": np.zeros((n, 4), dtype=np.int64),
                "attention_mask": np.ones((n, 4), dtype=np.int64),
            }

    class FakeSess:
        def run(self, names, feed):
            n = feed["input_ids"].shape[0]
            return [np.asarray([logit_fn(i) for i in range(n)], dtype=float).reshape(-1, 1)]

    return FakeTok(), FakeSess()


def test_rerank_onnx_orders_by_sigmoid(monkeypatch):
    import numpy as np
    from src.qa import reranker

    monkeypatch.setattr(reranker, "_get_onnx", lambda model_dir=None: _fake_onnx(lambda i: i - 1.0))
    docs = [{"id": f"c{i}", "text": f"文档{i}"} for i in range(3)]
    out = reranker.rerank_onnx("问题", docs, top_k=2)
    # logits: [-1, 0, 1] → sigmoid 升序 → 排序倒序，取 top2
    assert [h["id"] for h in out] == ["c2", "c1"]
    assert abs(out[0]["rerank_score"] - 1 / (1 + np.exp(-1.0))) < 1e-9
    assert out[0]["score"] == out[0]["rerank_score"]


def test_rerank_backend_auto_falls_back_to_torch(monkeypatch):
    from src.qa import reranker

    def boom(*a, **k):
        raise RuntimeError("onnx unavailable")

    called = {}

    def fake_torch(query, documents, top_k, model_name):
        called["torch"] = True
        return documents[:top_k]

    monkeypatch.setattr(reranker, "rerank_onnx", boom)
    monkeypatch.setattr(reranker, "_rerank_torch", fake_torch)

    docs = [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}, {"id": "c", "text": "z"}]
    out = reranker.rerank("q", docs, top_k=2, backend="auto")
    assert called.get("torch") is True
    assert len(out) == 2

    import pytest
    with pytest.raises(RuntimeError):
        reranker.rerank("q", docs, top_k=2, backend="onnx")
