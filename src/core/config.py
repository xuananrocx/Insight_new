"""配置加载模块。

负责：
1. 解析跨平台用户数据目录（mac/win/linux 统一）
2. 首次启动拷贝默认 config.yaml 到用户目录
3. config 版本化迁移
4. 加载 config.yaml（含本地覆盖）+ 路径展开
5. 维护 app.json 应用元数据

用户数据目录：
    mac:   ~/Library/Application Support/AI-Assistant/
    win:   %APPDATA%/AI-Assistant/   (= C:\\Users\\<user>\\AppData\\Roaming\\AI-Assistant\\)
    linux: ~/.local/share/AI-Assistant/
"""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sys
import yaml
from dotenv import load_dotenv
from platformdirs import user_data_dir

# 项目根目录：打包后从 _MEIPASS 找，开发期从源码上溯
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    PROJECT_ROOT = Path(sys._MEIPASS)
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 用户数据目录（跨平台）
APP_DIR_NAME = "AI-Assistant"
# Windows 下优先用 LOCALAPPDATA 环境变量：platformdirs 默认走 ctypes 的
# SHGetFolderPathW，在某些受限 shell（如沙箱 bash）里会静默失败返回 "."
# 导致数据目录落到 CWD；显式读环境变量与 platformdirs 结果一致且更稳。
# 测试隔离：tests/conftest.py 在 import src 之前设置 AMD_DATA_DIR 指向临时目录。
# 必须在这里（模块加载期）读取——USER_DATA_DIR 是 import 时解析的常量，
# 测试模块随后通过 settings.get_path 消费的都是这个根目录。
_env_data_dir = os.environ.get("AMD_DATA_DIR")
_win_local = os.environ.get("LOCALAPPDATA")
if _env_data_dir:
    USER_DATA_DIR = Path(_env_data_dir)
elif sys.platform == "win32" and _win_local:
    USER_DATA_DIR = Path(_win_local) / APP_DIR_NAME
else:
    USER_DATA_DIR = Path(user_data_dir(APP_DIR_NAME, appauthor=False))

# 出厂默认配置模板（项目源码里）
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
# 用户实际使用的配置
USER_CONFIG_PATH = USER_DATA_DIR / "config.yaml"
USER_CONFIG_LOCAL_PATH = USER_DATA_DIR / "config.local.yaml"
# 应用元数据
APP_META_PATH = USER_DATA_DIR / "app.json"

# 当前 config schema 版本（每次结构变更 +1）
CURRENT_CONFIG_VERSION = 3

# 启动时加载 .env（如果在）
load_dotenv(PROJECT_ROOT / ".env")


class ConfigError(Exception):
    """配置错误。"""


def _expand_path(p: str) -> Path:
    """展开路径：
    - `~` 开头：用户 home
    - `./` 或 `../` 开头：相对于 USER_DATA_DIR
    - 其他：按绝对路径处理
    """
    if p.startswith("~"):
        return Path(os.path.expanduser(p))
    if p.startswith("./") or p.startswith("../"):
        return (USER_DATA_DIR / p).resolve()
    return Path(p)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(
    base: dict, override: dict, _path: str = "", _changed: list[str] | None = None,
) -> dict:
    """深度合并 override 到 base，override 优先。

    如果传 _changed（list），会填充被 override 修改/新增的 dotted key path（用于审计日志）。
    """
    result = dict(base)
    for k, v in override.items():
        key_path = f"{_path}.{k}" if _path else k
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v, _path=key_path, _changed=_changed)
        else:
            result[k] = v
            if _changed is not None:
                _changed.append(key_path)
    return result


def _deep_merge_with_audit(base: dict, override: dict) -> tuple[dict, list[str]]:
    """带审计的 deep_merge：返回合并后的 dict 和被改动的 key path 列表。"""
    changed: list[str] = []
    merged = _deep_merge(base, override, _changed=changed)
    return merged, changed


# ===== config 版本化迁移 =====

# 迁移函数表：key = 起始版本，value = 转换函数
_CONFIG_MIGRATIONS: dict[int, Any] = {}


def _register_migration(from_version: int):
    """装饰器：注册 config 迁移函数。"""
    def decorator(fn):
        _CONFIG_MIGRATIONS[from_version] = fn
        return fn
    return decorator


def _migrate_config(cfg: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """按版本号依次跑迁移。返回 (新 config, 迁移日志)。"""
    logs: list[str] = []
    current = cfg.get("config_version", 0)
    while current < CURRENT_CONFIG_VERSION:
        fn = _CONFIG_MIGRATIONS.get(current)
        if fn is None:
            logs.append(f"warn: 无 v{current} → v{current + 1} 迁移函数，跳过")
            cfg["config_version"] = current + 1
            current += 1
            continue
        cfg = fn(cfg)
        cfg["config_version"] = current + 1
        logs.append(f"v{current} → v{current + 1}")
        current += 1
    return cfg, logs


# v2 收敛后的 provider 槽位
_V2_PROVIDER_SLOTS = ("deepseek", "openai", "anthropic", "glm", "custom")


@_register_migration(1)
def _migrate_v1_to_v2(cfg: dict[str, Any]) -> dict[str, Any]:
    """v2: LLM providers 从 8 个预设收敛为 5 槽位；新增超时配置。

    - 废弃槽位（qwen/moonshot/local/anthropic-arch）从用户配置剔除
    - anthropic-arch 若有实际配置，迁移进 custom 槽位（协议保留）
    - fallback_chain / chat_provider / embedding_provider 中的失效引用清理
    """
    llm = cfg.setdefault("llm", {})
    providers = llm.setdefault("providers", {})

    template_providers = _load_yaml(DEFAULT_CONFIG_PATH).get("llm", {}).get("providers", {})

    # anthropic-arch 的配置搬进 custom（仅当 custom 还没被用户配置过）
    arch_cfg = providers.pop("anthropic-arch", None)
    custom_cfg = providers.get("custom")
    if arch_cfg and not (custom_cfg and custom_cfg.get("base_url")):
        providers["custom"] = {**template_providers.get("custom", {}), **arch_cfg}

    # 补齐缺失槽位（新装的 custom 等）
    for slot in _V2_PROVIDER_SLOTS:
        if slot not in providers and slot in template_providers:
            providers[slot] = template_providers[slot]

    # 剔除废弃槽位
    for name in [k for k in providers if k not in _V2_PROVIDER_SLOTS]:
        providers.pop(name)
        print(f"[config 迁移] 剔除废弃 provider 槽位: {name}")

    # 清理引用：fallback 链里 anthropic-arch → custom，再过滤不存在的
    chain = ["custom" if n == "anthropic-arch" else n for n in llm.get("fallback_chain", [])]
    llm["fallback_chain"] = [n for n in chain if n in providers]

    if llm.get("chat_provider") not in providers:
        llm["chat_provider"] = "deepseek" if "deepseek" in providers else _V2_PROVIDER_SLOTS[0]
    if llm.get("embedding_provider") not in providers:
        llm["embedding_provider"] = "openai" if "openai" in providers else None

    # 超时配置（模板默认值兜底）
    tmpl_llm = _load_yaml(DEFAULT_CONFIG_PATH).get("llm", {})
    llm.setdefault("request_timeout_seconds", tmpl_llm.get("request_timeout_seconds", 120))
    llm.setdefault("test_timeout_seconds", tmpl_llm.get("test_timeout_seconds", 10))
    return cfg


@_register_migration(2)
def _migrate_v2_to_v3(cfg: dict[str, Any]) -> dict[str, Any]:
    """v3: 移除 provider 的 enabled 字段。

    provider 是否参与由「是否配置了 API Key」决定，启用开关已删除。
    """
    providers = cfg.get("llm", {}).get("providers", {})
    for p in providers.values():
        if isinstance(p, dict):
            p.pop("enabled", None)
    return cfg


# ===== app.json 应用元数据 =====

def _load_app_meta() -> dict[str, Any]:
    if not APP_META_PATH.exists():
        return {}
    try:
        with open(APP_META_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_app_meta(meta: dict[str, Any]) -> None:
    APP_META_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(APP_META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _update_app_meta(config_version: int) -> None:
    """每次启动更新 app.json。"""
    meta = _load_app_meta()
    is_first = not meta
    if is_first:
        meta["first_launch_at"] = datetime.now(timezone.utc).isoformat()
    meta["last_launch_at"] = datetime.now(timezone.utc).isoformat()
    meta["installed_version"] = _read_app_version_from_template()
    meta["config_version"] = config_version
    meta["user_data_dir"] = str(USER_DATA_DIR)
    _save_app_meta(meta)


def _read_app_version_from_template() -> str:
    """从默认 config 模板里读 app.version（避免循环依赖）。"""
    try:
        tmpl = _load_yaml(DEFAULT_CONFIG_PATH)
        return tmpl.get("app", {}).get("version", "unknown")
    except Exception:
        return "unknown"


# ===== 首次启动：拷贝默认模板 =====

def _ensure_user_config() -> tuple[dict[str, Any], list[str]]:
    """确保用户目录有 config.yaml。
    - 不存在：从默认模板拷贝，写入 config_version
    - 已存在：跑版本迁移
    返回 (config, logs)
    """
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    logs: list[str] = []

    if not USER_CONFIG_PATH.exists():
        logs.append("首次启动：从默认模板拷贝 config.yaml")
        shutil.copy2(DEFAULT_CONFIG_PATH, USER_CONFIG_PATH)
        # 写入 config_version
        cfg = _load_yaml(USER_CONFIG_PATH)
        cfg["config_version"] = CURRENT_CONFIG_VERSION
        with open(USER_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    else:
        cfg = _load_yaml(USER_CONFIG_PATH)
        old_version = cfg.get("config_version", 0)
        if old_version < CURRENT_CONFIG_VERSION:
            logs.append(f"config 版本过旧 (v{old_version})，开始迁移")
            cfg, migration_logs = _migrate_config(cfg)
            logs.extend(migration_logs)
            with open(USER_CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        elif old_version > CURRENT_CONFIG_VERSION:
            logs.append(f"warn: config 版本 (v{old_version}) 比代码 (v{CURRENT_CONFIG_VERSION}) 新")

    # 叠加本地覆盖（开发期用）
    local = _load_yaml(USER_CONFIG_LOCAL_PATH)
    if local:
        cfg, changed_keys = _deep_merge_with_audit(cfg, local)
        # 屏蔽敏感字段名（api_key 等）的具体值，只显示被覆盖了
        sensitive_keys = {"api_key", "secret", "token", "password"}
        safe_keys = [
            k if not any(s in k.lower() for s in sensitive_keys) else f"{k}（敏感）"
            for k in changed_keys
        ]
        logs.append(f"应用 config.local.yaml 覆盖：{', '.join(safe_keys) if safe_keys else '（无变更）'}")

    # 开发期：项目根目录的 config.local.yaml 也作为覆盖（保留旧机制）
    dev_local = _load_yaml(PROJECT_ROOT / "config.local.yaml")
    if dev_local:
        cfg, changed_keys = _deep_merge_with_audit(cfg, dev_local)
        sensitive_keys = {"api_key", "secret", "token", "password"}
        safe_keys = [
            k if not any(s in k.lower() for s in sensitive_keys) else f"{k}（敏感）"
            for k in changed_keys
        ]
        logs.append(
            f"应用项目根目录 config.local.yaml 覆盖（开发期）：{', '.join(safe_keys) if safe_keys else '（无变更）'}"
        )

    return cfg, logs


class Settings:
    """全局配置单例。

    使用：
        from src.core.config import settings
        print(settings.config["app"]["name"])
    """

    _instance: "Settings | None" = None

    def __init__(self) -> None:
        config, logs = _ensure_user_config()
        self._config: dict[str, Any] = config
        self._init_logs: list[str] = logs
        # 路径展开缓存
        self._paths_cache: dict[str, Path] = {}
        # 更新 app.json
        _update_app_meta(config.get("config_version", CURRENT_CONFIG_VERSION))

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

    @property
    def init_logs(self) -> list[str]:
        """初始化日志（启动时打印一次）。"""
        return self._init_logs

    # ===== 常用访问器 =====

    @property
    def app_name(self) -> str:
        return self._config.get("app", {}).get("name", "观澜 Insight")

    @property
    def app_version(self) -> str:
        return self._config.get("app", {}).get("version", "0.0.0")

    @property
    def log_level(self) -> str:
        return self._config.get("app", {}).get("log_level", "INFO")

    @property
    def api_url(self) -> str:
        s = self._config.get("server", {})
        host = s.get("api_host", "127.0.0.1")
        port = s.get("api_port", 8000)
        return f"http://{host}:{port}"

    @property
    def feed_folder(self) -> Path:
        return self._get_path("feed_folder", create=True)

    def get_path(self, key: str) -> Path:
        """通用路径访问器。"""
        return self._get_path(key)

    def _get_path(self, key: str, create: bool = False) -> Path:
        if key in self._paths_cache:
            return self._paths_cache[key]
        raw = self._config.get("paths", {}).get(key)
        if raw is None:
            raise ConfigError(
                f"配置缺失：paths.{key} 未定义。请检查 config.yaml 是否完整。"
            )
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
        """优先读直接配置的 api_key 字段，回退到 api_key_env 环境变量。"""
        direct_key = provider_cfg.get("api_key")
        if direct_key and direct_key.strip():
            return direct_key.strip()
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

    def is_enabled(self, dotted_key: str, default: bool = False) -> bool:
        """读取 feature flag，如 'knowledge_base.enabled'。

        路径不存在时返回 default（默认 False）。
        """
        node: Any = self._config.get("feature_flags", {})
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return bool(node)


# ===== 一次性数据迁移（开发期：把项目 ./data 拷到用户目录）=====

def migrate_legacy_data_once() -> list[str]:
    """开发期一次性迁移：如果项目目录有 ./data 且用户目录为空，拷贝过去。

    打包发布后，PROJECT_ROOT 是只读的（应用 bundle 内），不会有 ./data，
    所以这个函数在发布版中是 no-op。
    """
    logs: list[str] = []
    legacy_data = PROJECT_ROOT / "data"
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    marker = USER_DATA_DIR / ".migrated_v1"

    if marker.exists():
        return logs

    if not legacy_data.exists():
        marker.write_text(str(int(time.time())))
        return logs

    # config.yaml 里 paths 都是 `./data/xxx`，展开后是 USER_DATA_DIR/data/xxx
    user_data_subdir = USER_DATA_DIR / "data"

    # 检查用户目录是否已有实质数据
    if user_data_subdir.exists() and any(user_data_subdir.iterdir()):
        logs.append("用户目录已有数据，跳过遗留迁移")
        marker.write_text(str(int(time.time())))
        return logs

    user_data_subdir.mkdir(parents=True, exist_ok=True)
    logs.append(f"一次性迁移：{legacy_data} → {user_data_subdir}")
    for item in legacy_data.iterdir():
        target = user_data_subdir / item.name
        if target.exists():
            continue
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
        logs.append(f"  拷贝 {item.name}")

    marker.write_text(str(int(time.time())))
    return logs


# 全局单例
migrate_legacy_data_once()
settings = Settings.get()
