"""文档解析基础类型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ParsedSection:
    """文档中的一个段落/章节/页面。

    一份文档会被解析成多个 section，每个 section 有自己的文本和元信息。
    切片（chunking）会基于 section 进行，保留来源信息。
    """

    text: str
    source_path: Path
    section_index: int
    section_label: str = ""          # 例如 "第3页"、"表2"、"章节1.2"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "source_path": str(self.source_path),
            "section_index": self.section_index,
            "section_label": self.section_label,
            "extra": self.extra,
        }


@dataclass
class ParsedDocument:
    """一份文档的解析结果。"""

    source_path: Path
    sections: list[ParsedSection]
    file_type: str                   # pdf/docx/xlsx/...
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return "\n\n".join(s.text for s in self.sections if s.text.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "file_type": self.file_type,
            "title": self.title,
            "metadata": self.metadata,
            "sections": [s.to_dict() for s in self.sections],
        }


class ParseError(Exception):
    """文档解析失败。"""
