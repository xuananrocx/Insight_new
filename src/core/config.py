"""配置加载模块。

负责加载 config.yaml（含本地覆盖），展开路径，提供全局访问。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# 项目根目录（src/core/config.py 上溯两级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 启动时加载 .env（如果在）
load_dotenv(PROJECT_ROOT / ".env")


class ConfigError(Exception):
    """配置错误。"""


def _expand_path(p: str) -> Path:
    """展开 ~ 和相对路径，返回绝对路径。"""
    if p.startswith("~"):
        return Path(os.path.expanduser(p))
    if p.startswith("./") or p.startswith("../"):
        return (PROJECT_ROOT / p).resolve()
    return Path(p)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class Settings:
    """全局配置单例。

    使用：
        from src.core.config import settings
        print(settings.config["app"]["name"])
    """

    _instance: "Settings | None" = None

    def __init__(self) -> None:
        # 加载主配置
        config = _load_yaml(PROJECT_ROOT / "config.yaml")
        # 叠加本地覆盖
        local = _load_yaml(PROJECT_ROOT / "config.local.yaml")
        if local:
            config = _deep_merge(config, local)
        self._config: dict[str, Any] = config
        # 路径展开缓存
        self._paths_cache: dict[str, Path] = {}

    @classmethod
    def get(cls) -> "Settings":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置单例（测试用）。"""
        cls._instance = None

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    # ===== 常用访问器 =====

    @property
    def app_name(self) -> str:
        return self._config["app"]["name"]

    @property
    def app_version(self) -> str:
        return self._config["app"]["version"]

    @property
    def log_level(self) -> str:
        return self._config["app"]["log_level"]

    @property
    def api_url(self) -> str:
        s = self._config["server"]
        return f"http://{s['api_host']}:{s['api_port']}"

    @property
    def feed_folder(self) -> Path:
        return self._get_path("feed_folder", create=True)

    def get_path(self, key: str) -> Path:
        """通用路径访问器。"""
        return self._get_path(key)

    def _get_path(self, key: str, create: bool = False) -> Path:
        if key in self._paths_cache:
            return self._paths_cache[key]
        raw = self._config["paths"][key]
        p = _expand_path(raw)
        if create:
            p.mkdir(parents=True, exist_ok=True)
        self._paths_cache[key] = p
        return p

    def ensure_data_dirs(self) -> None:
        """确保所有数据目录存在。"""
        for k in ["vector_db", "extracted", "feedback_queue", "raw_uploads", "cache"]:
            p = self._get_path(k, create=True)
            p.mkdir(parents=True, exist_ok=True)
        # metadata.db 的父目录
        self._get_path("metadata_db").parent.mkdir(parents=True, exist_ok=True)

    # ===== LLM 相关 =====

    def get_llm_provider(self, name: str) -> dict[str, Any]:
        providers = self._config["llm"]["providers"]
        if name not in providers:
            raise ConfigError(f"LLM provider '{name}' 未配置")
        cfg = providers[name]
        if not cfg.get("enabled", False):
            raise ConfigError(f"LLM provider '{name}' 已禁用")
        return cfg

    def resolve_api_key(self, provider_cfg: dict[str, Any]) -> str | None:
        """根据 api_key_env 字段从环境变量读 key。"""
        env_name = provider_cfg.get("api_key_env")
        if not env_name:
            return None
        return os.getenv(env_name)

    @property
    def chat_provider(self) -> str:
        return self._config["llm"]["chat_provider"]

    @property
    def embedding_provider(self) -> str:
        return self._config["llm"]["embedding_provider"]

    @property
    def fallback_chain(self) -> list[str]:
        return self._config["llm"]["fallback_chain"]

    # ===== Feature flags =====

    def is_enabled(self, dotted_key: str) -> bool:
        """读取 feature flag，如 'knowledge_base.enabled'。"""
        node: Any = self._config["feature_flags"]
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
        return bool(node)


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并 override 到 base，override 优先。"""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# 全局单例
settings = Settings.get()
