"""PDF 解析：pypdf（文本）+ pdfplumber（表格）。

策略：
1. 先用 pypdf 提取每页文本（速度快、稳定）
2. 用 pdfplumber 提取表格（如果该页有表格，附加到该页 section 末尾）
3. 每个 PDF 页面作为一个 section，便于精确引用"出自第N页"
"""
from __future__ import annotations

from pathlib import Path

import pypdf
import pdfplumber

from src.knowledge.parsers.base import ParsedDocument, ParsedSection


def parse_pdf(path: Path) -> ParsedDocument:
    """解析 PDF。每个页面一个 section。"""
    reader = pypdf.PdfReader(str(path))
    total_pages = len(reader.pages)
    title = path.stem
    try:
        meta = reader.metadata
        if meta and meta.title:
            title = meta.title
    except Exception:
        meta = None

    # 用 pdfplumber 单独扫一遍表格
    page_tables: dict[int, list[str]] = {}
    try:
        with pdfplumber.open(str(path)) as pdf:
            for i, page in enumerate(pdf.pages):
                try:
                    tables = page.extract_tables()
                except Exception:
                    tables = []
                if tables:
                    rendered: list[str] = []
                    for t_idx, t in enumerate(tables):
                        rows = []
                        for row in t:
                            cells = [(c or "").strip() for c in row]
                            rows.append(" | ".join(cells))
                        rendered.append(
                            f"\n\n[表格 {t_idx + 1}]\n" + "\n".join(rows)
                        )
                    page_tables[i] = rendered
    except Exception:
        # pdfplumber 失败不影响主流程，pypdf 的纯文本结果依然可用
        page_tables = {}

    sections: list[ParsedSection] = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        table_text = "".join(page_tables.get(i, []))
        full_text = (text + table_text).strip()
        sections.append(
            ParsedSection(
                text=full_text,
                source_path=path,
                section_index=i,
                section_label=f"第 {i + 1} 页",
                extra={"page": i + 1},
            )
        )

    return ParsedDocument(
        source_path=path,
        sections=sections,
        file_type="pdf",
        title=title,
        metadata={
            "page_count": total_pages,
            "author": str(meta.author) if meta and meta.author else "",
        },
    )
