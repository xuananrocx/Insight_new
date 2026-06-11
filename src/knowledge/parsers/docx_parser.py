"""Word .docx 解析：python-docx。

策略：每个段落（paragraph）+ 表格按文档顺序合并。
为简化，将整个文档按"标题级别"切分为多个 section。
"""
from __future__ import annotations

from pathlib import Path

from docx import Document as DocxDocument

from src.knowledge.parsers.base import ParsedDocument, ParsedSection


def parse_docx(path: Path) -> ParsedDocument:
    """解析 .docx。按章节（标题）切片。"""
    doc = DocxDocument(str(path))
    title = path.stem
    core = doc.core_properties
    if core.title:
        title = core.title

    sections: list[ParsedSection] = []
    current_lines: list[str] = []
    current_label = "前言"
    section_idx = 0

    def _flush() -> None:
        nonlocal section_idx, current_lines, current_label
        if current_lines:
            text = "\n".join(current_lines).strip()
            if text:
                sections.append(
                    ParsedSection(
                        text=text,
                        source_path=path,
                        section_index=section_idx,
                        section_label=current_label,
                    )
                )
                section_idx += 1
        current_lines = []

    # 遍历文档主体（段落 + 表格）
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    body = doc.element.body
    para_idx = 0
    table_idx = 0
    paragraphs = doc.paragraphs
    tables = doc.tables

    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            if para_idx >= len(paragraphs):
                continue
            p = paragraphs[para_idx]
            para_idx += 1
            style = (p.style.name or "").lower() if p.style else ""
            text = p.text.strip()
            if not text:
                continue
            # Heading 1/2/3 → 开新 section
            if "heading" in style or style.startswith("title"):
                _flush()
                current_label = text[:60]
            current_lines.append(text)
        elif child.tag == qn("w:tbl"):
            if table_idx >= len(tables):
                continue
            t = tables[table_idx]
            table_idx += 1
            for row in t.rows:
                cells = [c.text.strip() for c in row.cells]
                current_lines.append(" | ".join(cells))
    _flush()

    # 文档没有任何标题 → 整个作为一个 section
    if not sections:
        all_text = "\n".join(p.text for p in paragraphs if p.text.strip())
        sections.append(
            ParsedSection(
                text=all_text,
                source_path=path,
                section_index=0,
                section_label="",
            )
        )

    return ParsedDocument(
        source_path=path,
        sections=sections,
        file_type="docx",
        title=title,
        metadata={
            "paragraph_count": len(paragraphs),
            "table_count": len(tables),
            "author": core.author or "",
        },
    )
