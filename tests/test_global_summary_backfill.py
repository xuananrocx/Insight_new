"""KB 全局摘要：缺文档摘要时的自动补齐路径。

场景复刻：投喂时 ingest.ai_summary 未启用 → document_meta 无摘要 →
generate_kb_global_summary 应从向量库捞 chunk 文本现场补齐，再汇总。
"""
import pytest

from src.db import metadata_db
from src.knowledge import ai_summarizer
from src.knowledge.ai_summarizer import generate_kb_global_summary
from src.knowledge.chunker import Chunk


class _FakeChatClient:
    def __init__(self, answer: str):
        self._answer = answer

    def chat(self, messages, **kwargs):
        return self._answer, "fake-chat-provider"


@pytest.fixture(autouse=True)
def _init_db():
    metadata_db.init_db()


def _make_kb_with_file(kb_id: str, *, with_summary: bool) -> int:
    """建 KB + 一份已入库（done）文件；with_summary 决定有无文档摘要。"""
    metadata_db.create_kb(kb_id, f"KB-{kb_id}", f"col_{kb_id}", source="user")
    file_id = metadata_db.upsert_file(
        relative_path="doc.pdf",
        absolute_path=rf"C:\feeds\{kb_id}\doc.pdf",
        content_hash=f"hash-{kb_id}",
        file_size=100,
        file_type="pdf",
        kb_id=kb_id,
    )
    metadata_db.set_file_processed(file_id, ["c1", "c2"])
    metadata_db.upsert_document_meta(
        file_id=file_id,
        kb_id=kb_id,
        processed_level="summarized" if with_summary else "raw",
    )
    if with_summary:
        metadata_db.update_document_summary(file_id, "已有摘要", "m", 5)
    return file_id


def test_backfills_missing_doc_summary_then_generates(monkeypatch):
    kb_id = "kb_backfill"
    file_id = _make_kb_with_file(kb_id, with_summary=False)
    called = {}

    def fake_fetch(collection_name, chunk_ids):
        called["collection"] = collection_name
        called["chunk_ids"] = chunk_ids
        return [Chunk(text="AMD AMA 文档正文内容。" * 30, metadata={})]

    def fake_summarize(**kwargs):
        called["summarize_file_id"] = kwargs["file_id"]
        metadata_db.update_document_summary(kwargs["file_id"], "补出的摘要", "fake", 10)
        res = ai_summarizer.SummarizeResult()
        res.ok = True
        return res

    monkeypatch.setattr(ai_summarizer, "_fetch_chunks_by_ids", fake_fetch)
    monkeypatch.setattr(ai_summarizer, "summarize_and_extract", fake_summarize)
    monkeypatch.setattr(
        ai_summarizer.llm_client,
        "get_client",
        lambda: _FakeChatClient("这是知识库全局摘要。" * 10),
    )

    result = generate_kb_global_summary(kb_id, force=True)

    assert result.ok
    assert result.doc_count == 1
    assert called["summarize_file_id"] == file_id
    assert called["collection"] == f"col_{kb_id}"
    assert called["chunk_ids"] == ["c1", "c2"]


def test_skips_docs_that_already_have_summary(monkeypatch):
    kb_id = "kb_has_summary"
    _make_kb_with_file(kb_id, with_summary=True)

    def _boom(**kwargs):
        raise AssertionError("已带摘要的文档不应被重新摘要")

    monkeypatch.setattr(ai_summarizer, "summarize_and_extract", _boom)
    monkeypatch.setattr(
        ai_summarizer.llm_client,
        "get_client",
        lambda: _FakeChatClient("这是知识库全局摘要。" * 10),
    )

    result = generate_kb_global_summary(kb_id, force=True)

    assert result.ok
    assert result.doc_count == 1


def test_fails_cleanly_when_kb_has_no_docs(monkeypatch):
    kb_id = "kb_empty"
    metadata_db.create_kb(kb_id, "空KB", f"col_{kb_id}", source="user")

    def _boom(**kwargs):
        raise AssertionError("空 KB 不应触发补摘要")

    monkeypatch.setattr(ai_summarizer, "summarize_and_extract", _boom)

    result = generate_kb_global_summary(kb_id, force=True)

    assert not result.ok
    assert "补摘要" in result.error


def test_existing_global_summary_short_circuits(monkeypatch):
    kb_id = "kb_existing_global"
    _make_kb_with_file(kb_id, with_summary=True)
    metadata_db.update_kb_global_summary(kb_id, "旧全局摘要", "old", 10)

    def _boom(*args, **kwargs):
        raise AssertionError("已有全局摘要且非 force 时不应做任何生成")

    monkeypatch.setattr(ai_summarizer, "_backfill_doc_summaries", _boom)

    result = generate_kb_global_summary(kb_id, force=False)

    assert result.ok
    assert result.error == "already_exists"
    assert result.summary == "旧全局摘要"
