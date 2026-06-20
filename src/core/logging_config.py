"""日志配置。

特性：
- RotatingFileHandler：单文件 10MB，保留 5 个 = 上限 50MB
- 同时输出到控制台（开发期方便）和文件
- 启动时清理 mtime > 7 天的旧 .log* 文件
- 日志级别可在运行时通过 set_log_level() 调整（前端可配）
- 默认 INFO，开发时改 DEBUG

格式：
    2026-06-13 21:34:56.789 INFO  [routes_sessions] POST /sessions id=xxx
"""
from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.core.config import USER_DATA_DIR

LOG_DIR = USER_DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"

MAX_BYTES = 10 * 1024 * 1024  # 10MB
BACKUP_COUNT = 5
CLEANUP_AFTER_DAYS = 7

LOG_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-5s [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_INITIALIZED = False
_CURRENT_LEVEL = logging.INFO
_CURRENT_LOG_FILE: Path | None = None


def setup_logging(default_level: str = "INFO", *, force: bool = False) -> None:
    """初始化全局日志配置。

    幂等：默认情况下已经初始化时只更新 level。
    当 LOG_FILE 变化（多实例/测试切换 USER_DATA_DIR）时自动重建 handler。
    force=True：强制重建（清掉旧 handler 重来），用于测试或多账号场景。
    """
    global _INITIALIZED, _CURRENT_LEVEL, _CURRENT_LOG_FILE

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    level_value = _level_from_string(default_level)
    _CURRENT_LEVEL = level_value

    # 检测 LOG_FILE 是否变了（USER_DATA_DIR 切换场景）
    log_file_changed = _CURRENT_LOG_FILE is not None and _CURRENT_LOG_FILE != LOG_FILE

    if _INITIALIZED and not force and not log_file_changed:
        # 已经初始化过，只更新 level
        set_log_level(default_level)
        return

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    root = logging.getLogger()
    root.setLevel(level_value)

    # 清掉默认 handler（避免重复输出）
    # force 模式或 LOG_FILE 变化：必须重建 file handler
    for h in list(root.handlers):
        try:
            h.close()
        except Exception:
            pass
    root.handlers.clear()

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level_value)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level_value)
    root.addHandler(console_handler)

    # 降低噪音库的级别
    for noisy in ("chromadb", "httpx", "httpcore", "urllib3", "watchfiles", "sentence_transformers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _INITIALIZED = True
    _CURRENT_LOG_FILE = LOG_FILE

    logger = logging.getLogger(__name__)
    logger.info(f"logging initialized: file={LOG_FILE} level={default_level}")


def set_log_level(level: str) -> None:
    """运行时调整日志级别（前端可配）。"""
    global _CURRENT_LEVEL
    level_value = _level_from_string(level)
    _CURRENT_LEVEL = level_value
    logging.getLogger().setLevel(level_value)
    for h in logging.getLogger().handlers:
        h.setLevel(level_value)
    logging.getLogger(__name__).info(f"log level changed: {level}")


def get_log_level() -> str:
    """返回当前日志级别字符串。"""
    return logging.getLevelName(_CURRENT_LEVEL)


def cleanup_old_logs() -> int:
    """启动时清理 mtime > CLEANUP_AFTER_DAYS 天的 *.log* 文件。返回清理的文件数。

    glob *.log* 覆盖：
    - app.log / app.log.1 / app.log.2 等（RotatingFileHandler 命名）
    - 未来可能新增的 qa.log / import.log 等子文件
    - 大小写不敏感（部分系统）
    """
    if not LOG_DIR.exists():
        return 0
    threshold = time.time() - CLEANUP_AFTER_DAYS * 86400
    removed = 0
    for f in LOG_DIR.glob("*.log*"):
        try:
            if f.stat().st_mtime < threshold:
                f.unlink()
                removed += 1
        except Exception as e:
            logging.getLogger(__name__).warning(f"清理日志文件失败 {f}: {e}")
    if removed:
        logging.getLogger(__name__).info(f"cleaned {removed} old log files (> {CLEANUP_AFTER_DAYS} days)")
    return removed


def _level_from_string(level: str) -> int:
    mapping = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    return mapping.get(level.upper(), logging.INFO)
