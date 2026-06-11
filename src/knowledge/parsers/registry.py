"""文件类型注册表：扩展名 → parser 函数。

设计原则：
- 每种格式一个纯 Python parser（避免 Unstructured 的系统依赖）
- 扩展名 → parser 的映射集中在 _PARSERS 字典
- 新增格式：实现 parse_xxx(path) 函数 + 在 _PARSERS 注册
- 配置文件中的 ingest.text_extensions 优先于默认白名单
"""
from __future__ import annotations

from pathlib import Path

from src.core.config import settings
from src.knowledge.parsers.base import ParseError, ParsedDocument

# 各 parser 在独立模块实现，下面 import 进来
from src.knowledge.parsers import (
    text_parser,
    pdf_parser,
    docx_parser,
    xlsx_parser,
    pptx_parser,
    html_parser,
    xml_parser,
)


# 扩展名 → parser 函数（无大小写区分）
_PARSERS: dict[str, callable] = {}


def _register(extensions: list[str], fn: callable) -> None:
    for ext in extensions:
        _PARSERS[ext.lower().lstrip(".")] = fn


# ===== 注册所有 parser =====
_register(["md", "markdown", "txt", "rst", "log"], text_parser.parse_text)
_register(
    ["py", "sh", "bash", "js", "ts", "go", "java", "c", "cpp", "h", "hpp",
     "sql", "json", "yaml", "yml", "ini", "cfg", "conf", "toml", "properties",
     "csv", "tsv", "env", "gitignore", "dockerfile"],
    text_parser.parse_text,
)
_register(["pdf"], pdf_parser.parse_pdf)
_register(["docx"], docx_parser.parse_docx)
_register(["xlsx", "xlsm"], xlsx_parser.parse_xlsx)
_register(["pptx"], pptx_parser.parse_pptx)
_register(["html", "htm"], html_parser.parse_html)
_register(["xml"], xml_parser.parse_xml)


def get_supported_extensions() -> set[str]:
    """返回当前所有支持的扩展名。"""
    return set(_PARSERS.keys())


def get_effective_text_extensions() -> set[str]:
    """从配置读取的"投喂白名单"。"""
    cfg = settings.config
    exts = cfg.get("ingest", {}).get("text_extensions", [])
    return {e.lower().lstrip(".") for e in exts}


def parse_file(path: Path) -> ParsedDocument:
    """根据扩展名分发到具体 parser。

    抛出：
        ParseError: 文件类型不支持或解析失败。
    """
    if not path.exists():
        raise ParseError(f"文件不存在: {path}")
    if not path.is_file():
        raise ParseError(f"不是文件: {path}")

    ext = path.suffix.lower().lstrip(".")
    fn = _PARSERS.get(ext)
    if fn is None:
        raise ParseError(
            f"不支持的文件类型: .{ext}（文件: {path.name}）"
        )
    try:
        return fn(path)
    except ParseError:
        raise
    except Exception as e:
        raise ParseError(f"解析 {path.name} (.{ext}) 失败: {e}") from e


def is_supported(path: Path) -> bool:
    """该文件是否可解析。"""
    ext = path.suffix.lower().lstrip(".")
    return ext in _PARSERS
