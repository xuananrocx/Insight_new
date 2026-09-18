"""KB 全局摘要过期检测（快照比对）。

生成摘要时存文件快照 {file_id: content_hash}；
之后 KB 文件集新增/删除/内容变更 → is_kb_summary_stale 为 True。
"""
import pytest

from src.db import metadata_db
from src.knowledge import ai_summarizer
from src.knowledge.ai_summarizer import generate_kb_global_summary


class _FakeChatClient:
    def chat(self, messages, **kwargs):
        return "这是知识库全局摘要内容。" * 10, "fake-chat-provider"


@pytest.fixture(autouse=True)
def _init_db():
    metadata_db.init_db()


def _make_kb_with_files(kb_id: str, n: int = 1) -> list[int]:
    metadata_db.create_kb(kb_id, f"KB-{kb_id}", f"col_{kb_id}", source="user")
    ids = []
    for i in range(n):
        file_id = metadata_db.upsert_file(
            relative_path=f"doc{i}.pdf",
            absolute_path=rf"C:\feeds\{kb_id}\doc{i}.pdf",
            content_hash=f"hash-{kb_id}-{i}",
            file_size=100,
            file_type="pdf",
            kb_id=kb_id,
        )
        metadata_db.set_file_processed(file_id, [f"c{i}"])
        metadata_db.upsert_document_meta(file_id=file_id, kb_id=kb_id, processed_level="summarized")
        metadata_db.update_document_summary(file_id, "文档摘要", "m", 5)
        ids.append(file_id)
    return ids


def _generate(monkeypatch, kb_id: str):
    monkeypatch.setattr(
        ai_summarizer.llm_client, "get_client", lambda: _FakeChatClient()
    )
    return generate_kb_global_summary(kb_id, force=True)


def test_fresh_summary_not_stale(monkeypatch):
    kb_id = "kb_stale_fresh"
    _make_kb_with_files(kb_id, n=2)
    assert _generate(monkeypatch, kb_id).ok
    assert metadata_db.is_kb_summary_stale(kb_id) is False


def test_new_file_makes_stale_then_rebuild_clears(monkeypatch):
    kb_id = "kb_stale_new"
    _make_kb_with_files(kb_id, n=1)
    assert _generate(monkeypatch, kb_id).ok

    file_id = metadata_db.upsert_file(
        relative_path="new.pdf",
        absolute_path=rf"C:\feeds\{kb_id}\new.pdf",
        content_hash="hash-new",
        file_size=100,
        file_type="pdf",
        kb_id=kb_id,
    )
    metadata_db.set_file_processed(file_id, ["cx"])
    metadata_db.upsert_document_meta(file_id=file_id, kb_id=kb_id, processed_level="summarized")
    metadata_db.update_document_summary(file_id, "新文档摘要", "m", 5)

    assert metadata_db.is_kb_summary_stale(kb_id) is True

    assert _generate(monkeypatch, kb_id).ok
    assert metadata_db.is_kb_summary_stale(kb_id) is False


def test_changed_hash_makes_stale(monkeypatch):
    kb_id = "kb_stale_change"
    _make_kb_with_files(kb_id, n=1)
    assert _generate(monkeypatch, kb_id).ok

    # 同路径重新 upsert（内容变更 → hash 变）
    metadata_db.upsert_file(
        relative_path="doc0.pdf",
        absolute_path=rf"C:\feeds\{kb_id}\doc0.pdf",
        content_hash="hash-changed",
        file_size=200,
        file_type="pdf",
        kb_id=kb_id,
    )
    assert metadata_db.is_kb_summary_stale(kb_id) is True


def test_legacy_summary_without_snapshot_not_stale(monkeypatch):
    """升级前生成的旧摘要（无快照）不应误报过期。"""
    kb_id = "kb_stale_legacy"
    _make_kb_with_files(kb_id, n=1)
    assert metadata_db.update_kb_global_summary(kb_id, "旧摘要", "m", 5)  # 不带 snapshot
    assert metadata_db.is_kb_summary_stale(kb_id) is False


def test_no_summary_not_stale():
    kb_id = "kb_stale_empty"
    _make_kb_with_files(kb_id, n=1)
    assert metadata_db.is_kb_summary_stale(kb_id) is False
