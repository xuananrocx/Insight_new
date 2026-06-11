"""文本切片器。

输入：ParsedDocument（已经按章节/页面拆分的 section 列表）
输出：Chunk 列表（每个 chunk 一段文本 + 完整来源元数据）

策略：
- RecursiveCharacterTextSplitter（中文友好的分隔符优先级）
- 每个 chunk 携带：source_path、section_label、page、chunk_index 等
- 切片不跨 section 边界（避免混淆来源）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.core.config import settings
from src.knowledge.parsers.base import ParsedDocument, ParsedSection


@dataclass
class Chunk:
    """一个向量入库的单元。"""

    text: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "metadata": self.metadata}


def _build_splitter() -> RecursiveCharacterTextSplitter:
    cfg = settings.config.get("ingest", {}).get("chunking", {})
    return RecursiveCharacterTextSplitter(
        chunk_size=cfg.get("chunk_size", 800),
        chunk_overlap=cfg.get("chunk_overlap", 100),
        separators=["\n\n", "\n", "。", "！", "？", "；", ". ", "! ", "? ", "; ", " ", ""],
        keep_separator=True,
    )


def chunk_document(doc: ParsedDocument) -> list[Chunk]:
    """把 ParsedDocument 切成 Chunk 列表。"""
    splitter = _build_splitter()
    chunks: list[Chunk] = []
    for section in doc.sections:
        text = section.text.strip()
        if not text:
            continue
        pieces = splitter.split_text(text)
        for i, piece in enumerate(pieces):
            meta = {
                "source_path": str(doc.source_path),
                "source_name": doc.source_path.name,
                "file_type": doc.file_type,
                "title": doc.title or doc.source_path.stem,
                "section_index": section.section_index,
                "section_label": section.section_label,
                "chunk_index": i,
                "chunk_total_in_section": len(pieces),
                # 额外元数据透传
                **{k: v for k, v in section.extra.items()
                   if isinstance(v, (str, int, float, bool))},
            }
            chunks.append(Chunk(text=piece, metadata=meta))
    return chunks
