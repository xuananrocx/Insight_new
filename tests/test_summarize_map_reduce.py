"""文档摘要的章节感知分段（map-reduce）逻辑。"""
import pytest

from src.db import metadata_db
from src.knowledge import ai_summarizer
from src.knowledge.ai_summarizer import (
    _split_chunks_by_sections,
    summarize_and_extract,
)
from src.knowledge.chunker import Chunk


def _chunk(text: str, label: str = "") -> Chunk:
    return Chunk(text=text, metadata={"section_label": label} if label else {})


# ===== 分段函数 =====

def test_split_single_segment_when_small():
    chunks = [_chunk("a" * 100), _chunk("b" * 100)]
    segs = _split_chunks_by_sections(chunks, 1000)
    assert len(segs) == 1
    assert len(segs[0]) == 2


def test_split_breaks_at_section_boundary_after_soft_limit():
    # cap=600（soft=480）：A 章 5 块共 500 字后遇到 B 章 → 在章节边界切
    chunks = [_chunk("a" * 100, "第1章")] * 5 + [_chunk("b" * 100, "第2章")] * 5
    segs = _split_chunks_by_sections(chunks, 600)
    assert len(segs) == 2
    assert all(c.metadata["section_label"] == "第1章" for c in segs[0])
    assert all(c.metadata["section_label"] == "第2章" for c in segs[1])


def test_split_hard_cap_when_single_section_too_long():
    # 同一章节超硬上限 → 强制切，不允许超长段
    chunks = [_chunk("a" * 200, "第1章")] * 10  # 2000 字
    segs = _split_chunks_by_sections(chunks, 600)
    assert len(segs) >= 3
    for seg in segs:
        assert sum(len(c.text) for c in seg) <= 600 + 200  # 最多溢出一个 chunk


def test_split_no_label_never_section_breaks():
    # 无章节标签的 chunks 只按硬上限切
    chunks = [_chunk("a" * 300)] * 10
    segs = _split_chunks_by_sections(chunks, 700)
    assert len(segs) >= 4


# ===== map-reduce 全流程 =====

class _FakeMapReduceClient:
    def __init__(self):
        self.kinds: list[str] = []
        self.user_contents: list[str] = []

    def chat(self, messages, **kwargs):
        is_map = "要点笔记" in messages[0]["content"]
        self.kinds.append("map" if is_map else "reduce")
        self.user_contents.append(messages[1]["content"])
        if is_map:
            return "该段覆盖 IAMDApi 初始化流程与 SetProperty 配置项。", "fake"
        return (
            '{"summary": "文档整体摘要，覆盖全部章节内容。", "concepts": '
            '[{"name": "SetProperty", "type": "command", "description": "配置接口"}]}'
        ), "fake"

    def embed(self, texts):
        return [[0.1] * 4 for _ in texts], "fake-embed"


@pytest.fixture(autouse=True)
def _init_db():
    metadata_db.init_db()


def _setup_kb(kb_id: str) -> int:
    metadata_db.create_kb(kb_id, f"KB-{kb_id}", f"col_{kb_id}", source="user")
    file_id = metadata_db.upsert_file(
        relative_path="big.pdf",
        absolute_path=rf"C:\feeds\{kb_id}\big.pdf",
        content_hash=f"hash-{kb_id}",
        file_size=10,
        file_type="pdf",
        kb_id=kb_id,
    )
    metadata_db.upsert_document_meta(file_id=file_id, kb_id=kb_id, processed_level="raw")
    return file_id


def test_large_doc_goes_map_reduce(monkeypatch):
    kb_id = "kb_map_reduce"
    file_id = _setup_kb(kb_id)
    fake = _FakeMapReduceClient()
    upserts: list = []

    def fake_upsert(chunks, collection_name):
        upserts.append((chunks, collection_name))
        return [c["id"] for c in chunks]

    monkeypatch.setattr(ai_summarizer.llm_client, "get_client", lambda: fake)
    monkeypatch.setattr(ai_summarizer.vector_store, "upsert_chunks", fake_upsert)
    monkeypatch.setattr(ai_summarizer, "_segment_chars", lambda: 3000)

    chunks = [
        _chunk("章节正文内容。" * 60, f"第{i // 10}章") for i in range(30)
    ]  # 30 × 420 字 = 12600 字

    res = summarize_and_extract(
        file_id=file_id,
        kb_id=kb_id,
        file_name="big.pdf",
        chunks=chunks,
        content_hash=f"hash-{kb_id}",
        collection_name=f"col_{kb_id}",
        source_path="big.pdf",
    )

    assert res.ok, res.error
    assert fake.kinds.count("map") >= 3
    assert fake.kinds.count("reduce") == 1
    assert "要点笔记（已覆盖文档全文）" in fake.user_contents[-1]
    assert upserts and upserts[0][0][0]["metadata"]["chunk_type"] == "summary"
    meta = metadata_db.list_document_meta_by_kb(kb_id)[0]
    assert meta["processed_level"] == "summarized"
    assert "全部章节" in meta["summary"]


def test_small_doc_single_call_full_text(monkeypatch):
    kb_id = "kb_single_call"
    file_id = _setup_kb(kb_id)
    fake = _FakeMapReduceClient()
    monkeypatch.setattr(ai_summarizer.llm_client, "get_client", lambda: fake)
    monkeypatch.setattr(
        ai_summarizer.vector_store,
        "upsert_chunks",
        lambda chunks, collection_name: [c["id"] for c in chunks],
    )

    chunks = [_chunk("短文档核心内容。" * 20, "第1章")]  # 160 字，远小于默认段长
    res = summarize_and_extract(
        file_id=file_id,
        kb_id=kb_id,
        file_name="small.pdf",
        chunks=chunks,
        content_hash=f"hash-{kb_id}",
        collection_name=f"col_{kb_id}",
        source_path="small.pdf",
    )

    assert res.ok, res.error
    assert fake.kinds == ["reduce"]  # 单次直出，无 map
    assert "文档已截断" not in fake.user_contents[0]
    assert "短文档核心内容" in fake.user_contents[0]
