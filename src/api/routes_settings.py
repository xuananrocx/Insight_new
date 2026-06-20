"""系统设置相关 API 路由。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.core.config import settings
from src.core.logging_config import get_log_level, set_log_level
from src.db import metadata_db
import logging


def _secure_write_yaml(config_path: Path, data: Any) -> None:
    """写 yaml 并收紧文件权限到 0600（仅所有者可读）。

    config.yaml 含 LLM api_key 等敏感字段，默认 0644 会被本机其他用户读到。
    """
    tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    # 用 0600 创建临时文件
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp_path, config_path)
        # rename 后权限可能变（受 umask 影响），再强制 chmod
        try:
            os.chmod(config_path, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _ensure_secure_permissions() -> None:
    """启动时检查 + 收紧敏感文件权限。"""
    from src.core.config import settings
    for path_like in [
        lambda: settings.get_path("config_file") if hasattr(settings, "get_path") else None,
    ]:
        try:
            p = path_like()
            if p and Path(p).exists():
                try:
                    os.chmod(p, 0o600)
                except OSError:
                    pass
        except Exception:
            pass

logger = logging.getLogger(__name__)
from src.qa.rag import (
    DEFAULT_SYSTEM_PROMPT,
    MAX_SYSTEM_PROMPT_LENGTH,
    ask,
)
from src.core import llm_client

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


class SystemPromptInfo(BaseModel):
    current: str
    default: str
    is_default: bool
    max_length: int


class UpdateSystemPromptRequest(BaseModel):
    system_prompt: str = Field(..., min_length=1)


class TestSystemPromptRequest(BaseModel):
    system_prompt: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1, max_length=4000)


class TestSourceModel(BaseModel):
    source_path: str
    source_name: str
    title: str
    section_label: str
    text_snippet: str
    score: float


class TestSystemPromptResponse(BaseModel):
    answer: str
    sources: list[TestSourceModel]
    used_provider: str
    used_chunks: int


def _read_current_prompt() -> str:
    cfg = settings.config.get("qa", {}) or {}
    return (cfg.get("system_prompt") or "").strip()


def _save_prompt_to_config(prompt: str) -> None:
    """写入用户目录 config.yaml + 同步内存。空字符串表示用默认。"""
    from src.core.config import USER_CONFIG_PATH
    config_path = USER_CONFIG_PATH
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    raw.setdefault("qa", {})["system_prompt"] = prompt
    settings.config.setdefault("qa", {})["system_prompt"] = prompt
    _secure_write_yaml(config_path, raw)


@router.get("/system_prompt", response_model=SystemPromptInfo)
def get_system_prompt() -> SystemPromptInfo:
    current = _read_current_prompt()
    return SystemPromptInfo(
        current=current or DEFAULT_SYSTEM_PROMPT,
        default=DEFAULT_SYSTEM_PROMPT,
        is_default=current == "",
        max_length=MAX_SYSTEM_PROMPT_LENGTH,
    )


@router.put("/system_prompt", response_model=SystemPromptInfo)
def update_system_prompt(req: UpdateSystemPromptRequest) -> SystemPromptInfo:
    prompt = req.system_prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="system prompt 不能为空")
    if len(prompt) > MAX_SYSTEM_PROMPT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"system prompt 长度不能超过 {MAX_SYSTEM_PROMPT_LENGTH} 字符",
        )
    _save_prompt_to_config(prompt)
    return SystemPromptInfo(
        current=prompt,
        default=DEFAULT_SYSTEM_PROMPT,
        is_default=False,
        max_length=MAX_SYSTEM_PROMPT_LENGTH,
    )


@router.post("/system_prompt/reset", response_model=SystemPromptInfo)
def reset_system_prompt() -> SystemPromptInfo:
    _save_prompt_to_config("")
    return SystemPromptInfo(
        current=DEFAULT_SYSTEM_PROMPT,
        default=DEFAULT_SYSTEM_PROMPT,
        is_default=True,
        max_length=MAX_SYSTEM_PROMPT_LENGTH,
    )


@router.post("/system_prompt/test", response_model=TestSystemPromptResponse)
def test_system_prompt(req: TestSystemPromptRequest) -> TestSystemPromptResponse:
    """用传入的 system prompt 试运行一次问答（不写入 config）。"""
    from src.knowledge import rebuild
    if rebuild.is_rebuilding():
        raise HTTPException(
            status_code=503,
            detail="向量库重建中，请等待重建完成后再测试",
        )
    try:
        result = ask(
            question=req.question,
            system_override=req.system_prompt,
            scene="test",
        )
    except llm_client.NoAvailableProviderError as e:
        raise HTTPException(
            status_code=503,
            detail=f"没有可用的 LLM provider。原因: {e}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"测试失败: {e}")

    return TestSystemPromptResponse(
        answer=result.answer,
        sources=[
            TestSourceModel(
                source_path=c.source_path,
                source_name=c.source_name,
                title=c.title,
                section_label=c.section_label,
                text_snippet=c.text_snippet,
                score=c.score,
            )
            for c in result.citations
        ],
        used_provider=result.used_provider,
        used_chunks=result.used_chunks,
    )


# ===== 日志级别配置 =====

class LogLevelInfo(BaseModel):
    current: str
    available: list[str]


class UpdateLogLevelRequest(BaseModel):
    level: str = Field(..., pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")


@router.get("/log_level", response_model=LogLevelInfo)
def get_log_level_endpoint() -> LogLevelInfo:
    return LogLevelInfo(
        current=get_log_level(),
        available=["DEBUG", "INFO", "WARNING", "ERROR"],
    )


@router.put("/log_level", response_model=LogLevelInfo)
def update_log_level_endpoint(req: UpdateLogLevelRequest) -> LogLevelInfo:
    set_log_level(req.level)
    # 持久化到 app_state，重启后恢复
    metadata_db.set_state("log_level", req.level)
    return LogLevelInfo(
        current=get_log_level(),
        available=["DEBUG", "INFO", "WARNING", "ERROR"],
    )


# ===== LLM Provider 管理 =====

class LLMProviderInfo(BaseModel):
    name: str
    enabled: bool
    has_chat: bool
    has_embedding: bool
    api_key_configured: bool
    api_key_preview: str | None  # 前 8 位，用于列表展示（如 "sk-3c1b16..."）
    api_key: str | None  # 完整 key，仅用于编辑框预填（本地工具，安全风险可接受）
    protocol: str
    base_url: str | None
    chat_model: str | None
    is_active: bool


class LLMProvidersResponse(BaseModel):
    current_provider: str
    providers: list[LLMProviderInfo]
    fallback_chain: list[str]


class SwitchProviderRequest(BaseModel):
    provider_name: str = Field(..., min_length=1)


class TestProviderRequest(BaseModel):
    provider_name: str = Field(..., min_length=1)


class TestProviderConfigRequest(BaseModel):
    name: str = Field(..., min_length=1)
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class UpdateProviderRequest(BaseModel):
    name: str = Field(..., min_length=1)
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    enabled: bool | None = None


@router.get("/llm_providers", response_model=LLMProvidersResponse)
def get_llm_providers() -> LLMProvidersResponse:
    """获取所有 LLM providers 状态。"""
    client = llm_client.get_client()
    health = llm_client.health_check()

    providers_info = []
    for name, info in health["providers"].items():
        # 从配置中获取更多信息
        provider_cfg = settings.config["llm"]["providers"].get(name, {})

        # 生成 API Key 预览（前 8 位）+ 完整 key（用于编辑框预填）
        api_key_preview = None
        full_api_key = None
        if info["api_key_configured"]:
            api_key = settings.resolve_api_key(provider_cfg)
            if api_key:
                full_api_key = api_key
                if len(api_key) >= 8:
                    api_key_preview = f"{api_key[:8]}..."

        providers_info.append(
            LLMProviderInfo(
                name=name,
                enabled=info["enabled"],
                has_chat=info["has_chat"],
                has_embedding=info["has_embedding"],
                api_key_configured=info["api_key_configured"],
                api_key_preview=api_key_preview,
                api_key=full_api_key,
                protocol=provider_cfg.get("protocol", "openai"),
                base_url=provider_cfg.get("base_url"),
                chat_model=provider_cfg.get("chat_model"),
                is_active=health.get("current_chat_provider") == name,
            )
        )

    return LLMProvidersResponse(
        current_provider=health.get("current_chat_provider", ""),
        providers=providers_info,
        fallback_chain=health.get("fallback_chain", []),
    )


@router.post("/llm_providers/switch")
def switch_provider(req: SwitchProviderRequest) -> LLMProvidersResponse:
    """切换活跃的 LLM provider。"""
    provider_name = req.provider_name
    logger.info(f"LLM Provider: 切换到 {provider_name}")

    # 验证 provider 存在且可用
    providers = settings.config["llm"]["providers"]
    if provider_name not in providers:
        logger.warning(f"LLM Provider: 切换失败，provider '{provider_name}' 不存在")
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' 不存在")

    provider_cfg = providers[provider_name]
    if not provider_cfg.get("enabled", False):
        logger.warning(f"LLM Provider: 切换失败，provider '{provider_name}' 未启用")
        raise HTTPException(status_code=400, detail=f"Provider '{provider_name}' 未启用")

    # 检查 API key
    api_key = settings.resolve_api_key(provider_cfg)
    if not api_key and provider_name != "local":
        logger.warning(f"LLM Provider: 切换失败，provider '{provider_name}' 缺少 API key")
        raise HTTPException(status_code=400, detail=f"Provider '{provider_name}' 缺少 API key")

    # 切换 provider
    try:
        _save_provider_to_config(provider_name)
        # 重置 client 单例，强制重新初始化
        llm_client.reset_client()
        logger.info(f"LLM Provider: 成功切换到 {provider_name}")
    except Exception as e:
        logger.error(f"LLM Provider: 切换到 {provider_name} 失败: {e}")
        raise HTTPException(status_code=500, detail=f"切换失败: {e}")

    # 返回更新后的状态
    return get_llm_providers()


@router.post("/llm_providers/test")
def test_provider(req: TestProviderRequest) -> dict[str, Any]:
    """测试 provider 连接是否可用。"""
    provider_name = req.provider_name
    logger.info(f"LLM Provider: 测试连接 {provider_name}")

    # 验证 provider 存在
    providers = settings.config["llm"]["providers"]
    if provider_name not in providers:
        logger.warning(f"LLM Provider: 测试失败，provider '{provider_name}' 不存在")
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' 不存在")

    try:
        client = llm_client.get_client()
        provider = client._providers.get(provider_name)
        if not provider:
            logger.warning(f"LLM Provider: 测试失败，provider '{provider_name}' 未初始化")
            return {"success": False, "error": "Provider 未初始化"}

        # 测试连接
        is_connected = provider.test_connection()
        if is_connected:
            logger.info(f"LLM Provider: 测试成功 {provider_name}")
        else:
            logger.warning(f"LLM Provider: 测试失败 {provider_name}")
        return {"success": is_connected, "provider": provider_name}
    except Exception as e:
        logger.error(f"LLM Provider: 测试异常 {provider_name}: {e}")
        return {"success": False, "error": str(e), "provider": provider_name}


@router.post("/llm_providers/test_config")
def test_provider_config(req: TestProviderConfigRequest) -> dict[str, Any]:
    """测试 provider 配置是否可用（不保存配置）。"""
    provider_name = req.name
    logger.info(f"LLM Provider: 测试配置 {provider_name}")

    # 验证 provider 存在
    providers = settings.config["llm"]["providers"]
    if provider_name not in providers:
        logger.warning(f"LLM Provider: 测试配置失败，provider '{provider_name}' 不存在")
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' 不存在")

    try:
        # 创建临时 provider 进行测试
        provider_cfg = providers[provider_name].copy()

        # 应用临时配置 + SSRF 校验
        if req.base_url:
            from src.core.security import validate_external_url, SecurityError
            try:
                # anthropic-arch 是国内代理，allow_http + allow_private_ip 给宽松
                is_arch = provider_cfg.get("protocol") == "anthropic-arch"
                allow_http = is_arch
                allow_private_ip = is_arch
                validated = validate_external_url(req.base_url, allow_http=allow_http, allow_private_ip=allow_private_ip)
                provider_cfg["base_url"] = validated
            except SecurityError as e:
                raise HTTPException(status_code=400, detail=f"base_url 不安全: {e}")
        if req.api_key:
            provider_cfg["api_key"] = req.api_key
        if req.model:
            provider_cfg["chat_model"] = req.model

        # 根据协议创建临时 provider
        protocol = provider_cfg.get("protocol", "openai")
        if protocol == "anthropic-arch":
            from src.core.llm_providers import AnthropicArchProvider
            temp_provider = AnthropicArchProvider(
                name=provider_name,
                cfg=provider_cfg,
            )
        elif protocol == "anthropic":
            from src.core.llm_providers import AnthropicProvider
            temp_provider = AnthropicProvider(
                name=provider_name,
                cfg=provider_cfg,
            )
        else:
            from src.core.llm_providers import OpenAIProvider
            temp_provider = OpenAIProvider(
                name=provider_name,
                cfg=provider_cfg,
            )

        # 测试连接
        is_connected = temp_provider.test_connection()
        if is_connected:
            logger.info(f"LLM Provider: 配置测试成功 {provider_name}")
        else:
            logger.warning(f"LLM Provider: 配置测试失败 {provider_name}")
        return {"success": is_connected, "provider": provider_name}
    except Exception as e:
        logger.error(f"LLM Provider: 配置测试异常 {provider_name}: {e}")
        return {"success": False, "error": str(e), "provider": provider_name}


@router.post("/llm_providers/update")
def update_provider(req: UpdateProviderRequest) -> dict[str, str]:
    """更新 provider 配置。"""
    provider_name = req.name
    logger.info(f"LLM Provider: 更新配置 {provider_name}")

    # 验证 provider 存在
    providers = settings.config["llm"]["providers"]
    if provider_name not in providers:
        logger.warning(f"LLM Provider: 更新失败，provider '{provider_name}' 不存在")
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' 不存在")

    provider_cfg = providers[provider_name]

    # 记录配置变更
    changes = []
    if req.base_url is not None:
        # SSRF 校验：禁止 base_url 指向内网/回环/云元数据
        from src.core.security import validate_external_url, SecurityError
        try:
            is_arch = provider_cfg.get("protocol") == "anthropic-arch"
            allow_http = is_arch
            allow_private_ip = is_arch
            validated = validate_external_url(req.base_url, allow_http=allow_http, allow_private_ip=allow_private_ip)
        except SecurityError as e:
            raise HTTPException(status_code=400, detail=f"base_url 不安全: {e}")
        changes.append(f"base_url: {provider_cfg.get('base_url')} → {validated}")
        provider_cfg["base_url"] = validated

    if req.model is not None:
        changes.append(f"chat_model: {provider_cfg.get('chat_model')} → {req.model}")
        provider_cfg["chat_model"] = req.model

    if req.enabled is not None:
        changes.append(f"enabled: {provider_cfg.get('enabled')} → {req.enabled}")
        provider_cfg["enabled"] = req.enabled

    # 处理 API Key：留空=保持不变，非空=更新
    if req.api_key is not None and req.api_key.strip():
        changes.append("api_key: *** → *** (更新)")
        provider_cfg["api_key"] = req.api_key

    if changes:
        logger.info(f"LLM Provider: 配置变更 {provider_name}: {', '.join(changes)}")

    # 保存到配置文件
    try:
        _save_providers_config_to_file()
        # 重新初始化 LLM 客户端以应用更改
        llm_client.reset_client()
        logger.info(f"LLM Provider: 配置已保存 {provider_name}")
    except Exception as e:
        logger.error(f"LLM Provider: 保存失败 {provider_name}: {e}")
        raise HTTPException(status_code=500, detail=f"更新失败: {e}")

    return {"name": provider_name}


def _save_provider_to_config(provider_name: str) -> None:
    """写入用户目录 config.yaml 的 llm.chat_provider 字段。"""
    from src.core.config import USER_CONFIG_PATH
    config_path = USER_CONFIG_PATH
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    raw.setdefault("llm", {})["chat_provider"] = provider_name
    # 同步内存
    settings.config.setdefault("llm", {})["chat_provider"] = provider_name
    _secure_write_yaml(config_path, raw)


def _save_providers_config_to_file() -> None:
    """保存所有 providers 配置到用户目录 config.yaml。"""
    from src.core.config import USER_CONFIG_PATH
    config_path = USER_CONFIG_PATH
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # 保存所有 provider 配置
    raw.setdefault("llm", {})["providers"] = settings.config["llm"]["providers"]

    _secure_write_yaml(config_path, raw)


# ===== 检索策略（迭代 3）=====


class RetrievalStrategyInfo(BaseModel):
    kb_id: str
    strategy: str  # 'basic' | 'summary' | 'agentic'
    available_strategies: list[str] = ["basic", "summary", "agentic"]


class UpdateRetrievalStrategyRequest(BaseModel):
    kb_id: str = Field(..., min_length=1)
    strategy: str = Field(..., pattern="^(basic|summary|agentic)$")


@router.get("/retrieval_strategy", response_model=RetrievalStrategyInfo)
def get_retrieval_strategy(kb_id: str = "default") -> RetrievalStrategyInfo:
    """读取 KB 当前检索策略。"""
    metadata_db.init_db()
    kb = metadata_db.get_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    return RetrievalStrategyInfo(
        kb_id=kb_id,
        strategy=metadata_db.get_kb_retrieval_strategy(kb_id),
        available_strategies=["basic", "summary", "agentic"],
    )


@router.put("/retrieval_strategy", response_model=RetrievalStrategyInfo)
def update_retrieval_strategy(req: UpdateRetrievalStrategyRequest) -> RetrievalStrategyInfo:
    """更新 KB 检索策略。"""
    metadata_db.init_db()
    kb = metadata_db.get_kb(req.kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {req.kb_id}")
    try:
        metadata_db.update_kb(req.kb_id, retrieval_strategy=req.strategy)
        logger.info(f"KB {req.kb_id} 检索策略 → {req.strategy}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RetrievalStrategyInfo(
        kb_id=req.kb_id,
        strategy=req.strategy,
        available_strategies=["basic", "summary", "agentic"],
    )


# ===== AI 摘要配置（迭代 2 设置页 UI 补丁）=====


class AISummaryConfig(BaseModel):
    enabled: bool
    min_word_count: int = 100


@router.get("/ai_summary", response_model=AISummaryConfig)
def get_ai_summary_config() -> AISummaryConfig:
    """读取 AI 摘能配置。"""
    cfg = settings.config.get("ingest", {}).get("ai_summary", {}) or {}
    return AISummaryConfig(
        enabled=bool(cfg.get("enabled", False)),
        min_word_count=int(cfg.get("min_word_count", 100)),
    )


@router.put("/ai_summary", response_model=AISummaryConfig)
def update_ai_summary_config(req: AISummaryConfig) -> AISummaryConfig:
    """更新 AI 摘要配置（写入 config.yaml）。"""
    if req.min_word_count < 10:
        raise HTTPException(status_code=400, detail="min_word_count 必须 >= 10")
    from src.core.config import USER_CONFIG_PATH
    config_path = USER_CONFIG_PATH
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    raw.setdefault("ingest", {}).setdefault("ai_summary", {})
    raw["ingest"]["ai_summary"]["enabled"] = req.enabled
    raw["ingest"]["ai_summary"]["min_word_count"] = req.min_word_count
    # 同步内存
    settings.config.setdefault("ingest", {}).setdefault("ai_summary", {})
    settings.config["ingest"]["ai_summary"]["enabled"] = req.enabled
    settings.config["ingest"]["ai_summary"]["min_word_count"] = req.min_word_count
    _secure_write_yaml(config_path, raw)
    logger.info(f"AI 摘要配置: enabled={req.enabled}, min_word_count={req.min_word_count}")
    return AISummaryConfig(enabled=req.enabled, min_word_count=req.min_word_count)


# ===== UI 默认知识库 =====

class DefaultKbInfo(BaseModel):
    kb_id: str | None  # null/空 = 用 is_default 字段
    name: str | None  # 解析后的 KB 名（方便前端直接显示）
    source: str  # "config" 显式配置 / "is_default" 用默认字段


class UpdateDefaultKbRequest(BaseModel):
    kb_id: str | None  # null = 回退到 is_default 字段


def _resolve_default_kb() -> tuple[str | None, str | None, str]:
    """解析当前默认 KB。

    优先级：
    1. config.ui.default_kb_id 非空且对应 KB 存在 → 用它
    2. kbs 表里 is_default=1 的 KB → 用它
    3. 都没有 → null
    """
    metadata_db.init_db()
    configured = (settings.config.get("ui") or {}).get("default_kb_id")
    if configured:
        kb = metadata_db.get_kb(configured)
        if kb:
            return configured, kb["name"], "config"
        # 配置失效（KB 被删了）：fall through 到 is_default
    # fallback: 找 is_default=1
    for kb in metadata_db.list_kbs():
        if kb.get("is_default"):
            return kb["id"], kb["name"], "is_default"
    return None, None, "is_default"


@router.get("/default_kb", response_model=DefaultKbInfo)
def get_default_kb() -> DefaultKbInfo:
    """读取当前默认知识库。"""
    kb_id, name, source = _resolve_default_kb()
    return DefaultKbInfo(kb_id=kb_id, name=name, source=source)


@router.put("/default_kb", response_model=DefaultKbInfo)
def update_default_kb(req: UpdateDefaultKbRequest) -> DefaultKbInfo:
    """配置默认知识库（写入 config.yaml）。

    kb_id=null 清空配置，回退到 is_default 字段。
    kb_id 必须是已存在的 KB。
    """
    metadata_db.init_db()
    kb_id = req.kb_id.strip() if req.kb_id else None
    if kb_id:
        kb = metadata_db.get_kb(kb_id)
        if not kb:
            raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
    from src.core.config import USER_CONFIG_PATH
    config_path = USER_CONFIG_PATH
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    raw.setdefault("ui", {})["default_kb_id"] = kb_id
    # 同步内存
    settings.config.setdefault("ui", {})["default_kb_id"] = kb_id
    _secure_write_yaml(config_path, raw)
    resolved_id, name, source = _resolve_default_kb()
    logger.info(f"默认知识库配置已更新: kb_id={resolved_id} (source={source})")
    return DefaultKbInfo(kb_id=resolved_id, name=name, source=source)
