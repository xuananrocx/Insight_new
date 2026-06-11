"""XML 解析：lxml，提取文本内容（去标签）。"""
from __future__ import annotations

from pathlib import Path

from lxml import etree

from src.knowledge.parsers.base import ParsedDocument, ParsedSection


def parse_xml(path: Path) -> ParsedDocument:
    """解析 XML，提取所有文本节点。"""
    raw = path.read_bytes()
    root = etree.fromstring(raw)
    # 提取所有叶子节点的文本
    texts: list[str] = []
    for el in root.iter():
        tag = etree.QName(el).localname or ""
        text = (el.text or "").strip()
        if text:
            texts.append(f"[{tag}] {text}")
    return ParsedDocument(
        source_path=path,
        sections=[
            ParsedSection(
                text="\n".join(texts),
                source_path=path,
                section_index=0,
                section_label="",
            )
        ] if texts else [],
        file_type="xml",
        title=path.stem,
        metadata={"byte_size": path.stat().st_size},
    )
