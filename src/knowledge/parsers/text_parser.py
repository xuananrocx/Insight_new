"""纯文本/Markdown/代码/配置类文件解析。

这些文件结构简单，直接读取内容，作为单个 section 返回。
对于较大的文本（如 >1000 行），按行数切片。
"""
from __future__ import annotations

from pathlib import Path

from src.knowledge.parsers.base import ParsedDocument, ParsedSection


_MAX_LINES_PER_SECTION = 500


def parse_text(path: Path) -> ParsedDocument:
    """读取文本文件，大文件按行数切片。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        # 二进制被误判为文本时，UTF-8 替换后大概率内容混乱，跳过
        text = ""

    lines = text.splitlines()
    sections: list[ParsedSection] = []

    if len(lines) <= _MAX_LINES_PER_SECTION:
        sections.append(
            ParsedSection(
                text=text,
                source_path=path,
                section_index=0,
                section_label="",
            )
        )
    else:
        for i in range(0, len(lines), _MAX_LINES_PER_SECTION):
            chunk_lines = lines[i : i + _MAX_LINES_PER_SECTION]
            sections.append(
                ParsedSection(
                    text="\n".join(chunk_lines),
                    source_path=path,
                    section_index=i // _MAX_LINES_PER_SECTION,
                    section_label=f"行 {i + 1}-{min(i + _MAX_LINES_PER_SECTION, len(lines))}",
                )
            )

    return ParsedDocument(
        source_path=path,
        sections=sections,
        file_type=path.suffix.lower().lstrip("."),
        title=path.stem,
        metadata={"line_count": len(lines), "byte_size": path.stat().st_size},
    )
