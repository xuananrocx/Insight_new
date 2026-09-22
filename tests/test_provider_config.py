"""v2 LLM 槽位收敛相关测试：URL 识别 + config v1→v2 迁移。"""

from src.core.config import (
    CURRENT_CONFIG_VERSION,
    _migrate_config,
)
from src.core.llm_providers import _normalize_openai_base_url


# ===== _normalize_openai_base_url =====


def test_normalize_plain_v1_url():
    """填到 /v1：默认 chat 风格。"""
    assert _normalize_openai_base_url("https://api.openai.com/v1") == (
        "https://api.openai.com/v1",
        "chat",
    )


def test_normalize_bare_origin_uses_v1():
    assert _normalize_openai_base_url("https://example.test") == ("https://example.test/v1", "chat")
    assert _normalize_openai_base_url("https://example.test/") == ("https://example.test/v1", "chat")
    assert _normalize_openai_base_url("http://localhost:1234") == ("http://localhost:1234/v1", "chat")


def test_normalize_explicit_endpoints_and_custom_prefixes_are_preserved():
    for base in ("https://example.test/api/paas/v4", "https://example.test/compatible-mode/v1"):
        assert _normalize_openai_base_url(base) == (base, "chat")
    assert _normalize_openai_base_url("https://example.test/chat/completions") == ("https://example.test", "chat")
    assert _normalize_openai_base_url("https://example.test/responses") == ("https://example.test", "responses")


def test_normalize_chat_completions_suffix():
    """完整端点 /v1/chat/completions：去掉后缀。"""
    assert _normalize_openai_base_url(
        "https://fangfang.xuanan.org/v1/chat/completions"
    ) == ("https://fangfang.xuanan.org/v1", "chat")


def test_normalize_responses_suffix():
    """完整端点 /v1/responses：识别为 Responses API。"""
    assert _normalize_openai_base_url(
        "https://fangfang.xuanan.org/v1/responses"
    ) == ("https://fangfang.xuanan.org/v1", "responses")


def test_normalize_trailing_slash():
    """末尾斜杠应被剥离后再判断。"""
    assert _normalize_openai_base_url("https://x.example.com/v1/responses/") == (
        "https://x.example.com/v1",
        "responses",
    )


def test_normalize_empty_url():
    """空 URL 兜底为 chat 风格（后续由可用性检查拦截）。"""
    assert _normalize_openai_base_url("") == ("", "chat")
    assert _normalize_openai_base_url(None) == ("", "chat")


# ===== _migrate_v1_to_v2 =====


def _make_v1_config() -> dict:
    """构造一份典型 v1 配置（8 provider 全在，引用废弃槽位）。"""
    return {
        "config_version": 1,
        "llm": {
            "chat_provider": "qwen",
            "embedding_provider": "moonshot",
            "fallback_chain": ["deepseek", "qwen", "anthropic-arch", "moonshot"],
            "providers": {
                "deepseek": {
                    "api_key_env": "DEEPSEEK_API_KEY",
                    "base_url": "https://api.deepseek.com/v1",
                    "chat_model": "deepseek-chat",
                    "enabled": True,
                },
                "openai": {
                    "api_key_env": "OPENAI_API_KEY",
                    "base_url": "https://api.openai.com/v1",
                    "chat_model": "gpt-4o-mini",
                    "enabled": True,
                },
                "anthropic": {
                    "protocol": "anthropic",
                    "api_key_env": "ANTHROPIC_API_KEY",
                    "enabled": False,
                },
                "glm": {
                    "api_key_env": "ZHIPU_API_KEY",
                    "base_url": "https://open.bigmodel.cn/api/paas/v4",
                    "chat_model": "glm-4-plus",
                    "enabled": True,
                },
                "qwen": {
                    "api_key_env": "DASHSCOPE_API_KEY",
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "chat_model": "qwen-plus",
                    "enabled": True,
                },
                "moonshot": {
                    "api_key_env": "MOONSHOT_API_KEY",
                    "base_url": "https://api.moonshot.cn/v1",
                    "chat_model": "moonshot-v1-8k",
                    "enabled": False,
                },
                "local": {"base_url": "http://127.0.0.1:11434/v1", "enabled": False},
                "anthropic-arch": {
                    "protocol": "anthropic-arch",
                    "api_key_env": "CUSTOM_API_KEY",
                    "base_url": "https://fangfang.xuanan.org",
                    "chat_model": "my-model",
                    "enabled": True,
                },
            },
        },
    }


def test_migrate_prunes_deprecated_slots():
    """qwen/moonshot/local/anthropic-arch 应被剔除，只剩 5 槽位。"""
    cfg, logs = _migrate_config(_make_v1_config())
    providers = cfg["llm"]["providers"]
    assert set(providers.keys()) == {"deepseek", "openai", "anthropic", "glm", "custom"}
    assert any("v1 → v2" in line for line in logs)
    assert cfg["config_version"] == CURRENT_CONFIG_VERSION


def test_migrate_moves_arch_into_custom():
    """anthropic-arch 的实际配置应搬进 custom 槽位。"""
    cfg, _ = _migrate_config(_make_v1_config())
    custom = cfg["llm"]["providers"]["custom"]
    assert custom["protocol"] == "anthropic-arch"
    assert custom["base_url"] == "https://fangfang.xuanan.org"
    assert custom["chat_model"] == "my-model"


def test_migrate_keeps_existing_custom():
    """custom 已有配置时不被 anthropic-arch 覆盖。"""
    v1 = _make_v1_config()
    v1["llm"]["providers"]["custom"] = {
        "protocol": "openai",
        "base_url": "https://my-third-party.example.com/v1",
        "chat_model": "foo",
        "enabled": True,
    }
    cfg, _ = _migrate_config(v1)
    custom = cfg["llm"]["providers"]["custom"]
    assert custom["base_url"] == "https://my-third-party.example.com/v1"
    assert custom["protocol"] == "openai"


def test_migrate_fixes_references():
    """fallback_chain 引用清理；chat/embedding provider 落到可用槽位。"""
    cfg, _ = _migrate_config(_make_v1_config())
    llm = cfg["llm"]
    # anthropic-arch → custom，qwen/moonshot 被过滤
    assert llm["fallback_chain"] == ["deepseek", "custom"]
    assert llm["chat_provider"] == "deepseek"
    assert llm["embedding_provider"] == "openai"


def test_migrate_adds_timeout_keys():
    """超时配置缺失时应补默认值。"""
    cfg, _ = _migrate_config(_make_v1_config())
    assert cfg["llm"]["request_timeout_seconds"] > 0
    assert cfg["llm"]["test_timeout_seconds"] > 0


def test_migrate_version0_jumps_to_current():
    """无 config_version 字段（视为 v0）也能一路迁到当前版本。"""
    v1 = _make_v1_config()
    del v1["config_version"]
    cfg, logs = _migrate_config(v1)
    assert cfg["config_version"] == CURRENT_CONFIG_VERSION
    assert set(cfg["llm"]["providers"].keys()) == {
        "deepseek",
        "openai",
        "anthropic",
        "glm",
        "custom",
    }


def test_migrate_v3_strips_enabled_flags():
    """v3 迁移应剥掉所有 provider 的 enabled 字段。"""
    cfg, _ = _migrate_config(_make_v1_config())
    for p in cfg["llm"]["providers"].values():
        assert "enabled" not in p
