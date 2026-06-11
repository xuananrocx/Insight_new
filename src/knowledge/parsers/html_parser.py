"""HTML 解析：BeautifulSoup，提取纯文本。"""
from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup

from src.knowledge.parsers.base import ParsedDocument, ParsedSection


def parse_html(path: Path) -> ParsedDocument:
    """解析 HTML，去掉脚本和样式，提取文本。"""
    raw = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(raw, "html.parser")
    # 去掉 script / style
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    title = path.stem
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    text = soup.get_text(separator="\n", strip=True)
    sections = [
        ParsedSection(
            text=text,
            source_path=path,
            section_index=0,
            section_label=title,
        )
    ] if text else []
    return ParsedDocument(
        source_path=path,
        sections=sections,
        file_type="html",
        title=title,
        metadata={"byte_size": path.stat().st_size},
    )
