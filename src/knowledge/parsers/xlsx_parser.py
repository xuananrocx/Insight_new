"""Excel .xlsx 解析：openpyxl。

策略：每个 sheet 一个 section，内容以 Markdown 表格形式呈现。
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from src.knowledge.parsers.base import ParsedDocument, ParsedSection


def _row_to_md(cells: list) -> str:
    cells_str = [("" if c is None else str(c)).replace("|", "\\|").replace("\n", " ") for c in cells]
    return "| " + " | ".join(cells_str) + " |"


def parse_xlsx(path: Path) -> ParsedDocument:
    """解析 .xlsx，每个 sheet 一个 section。"""
    wb = load_workbook(str(path), data_only=True, read_only=True)
    sections: list[ParsedSection] = []
    for idx, sheet_name in enumerate(wb.sheetnames):
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        md_lines = [_row_to_md(list(r)) for r in rows]
        # 第二行后加分隔行（Markdown 表格语法）
        if len(md_lines) >= 1:
            col_count = len(rows[0])
            md_lines.insert(1, "| " + " | ".join(["---"] * col_count) + " |")
        sections.append(
            ParsedSection(
                text="\n".join(md_lines),
                source_path=path,
                section_index=idx,
                section_label=f"Sheet: {sheet_name}",
                extra={"sheet": sheet_name, "row_count": len(rows)},
            )
        )
    wb.close()
    return ParsedDocument(
        source_path=path,
        sections=sections,
        file_type="xlsx",
        title=path.stem,
        metadata={"sheet_count": len(wb.sheetnames)},
    )
