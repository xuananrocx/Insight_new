"""LLM 客户端 - 支持 OpenAI 兼容协议的多 provider。

支持：DeepSeek / Qwen (DashScope) / Moonshot / OpenAI / 本地 Ollama 或 vLLM
所有 provider 都通过 openai SDK 调用（兼容协议），切换只改配置。

架构约束：
    LLM client 只在 FastAPI 主进程内使用。
    Streamlit 等其他进程通过 HTTP 调 /api/v1/qa/ask 等接口，不直接调 LLM。
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from openai import OpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.core.config import ConfigError, settings


class LLMError(Exception):
    """LLM 调用失败。"""


class NoAvailableProviderError(LLMError):
    """所有 provider 都不可用。"""


class _Provider:
    """单个 LLM provider 的封装。"""

    def __init__(self, name: str, cfg: dict[str, Any]) -> None:
        self.name = name
        self.cfg = cfg
        self.base_url = cfg["base_url"]
        self.chat_model = cfg.get("chat_model")
        self.embedding_model = cfg.get("embedding_model")
        self._api_key = settings.resolve_api_key(cfg)
        self._client: OpenAI | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled", False))

    @property
    def has_api_key(self) -> bool:
        # 本地 provider（Ollama/vLLM）通常不需要 key
        if self.name == "local":
            return True
        return bool(self._api_key)

    @property
    def usable(self) -> bool:
        return self.enabled and self.has_api_key

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=self._api_key or "not-required",
                base_url=self.base_url,
                http_client=httpx.Client(timeout=120.0),
            )
        return self._client

    def chat(self, messages: list[dict], **kwargs: Any) -> str:
        if not self.chat_model:
            raise LLMError(f"provider {self.name} 未配置 chat_model")
        try:
            resp = self.client.chat.completions.create(
                model=self.chat_model,
                messages=messages,
                **kwargs,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            raise LLMError(f"provider {self.name} chat 失败: {e}") from e

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.embedding_model:
            raise LLMError(f"provider {self.name} 未配置 embedding_model")
        if self.embedding_model is False:
            raise LLMError(f"provider {self.name} 不支持 embedding")
        try:
            resp = self.client.embeddings.create(
                model=self.embedding_model,
                input=texts,
            )
            return [d.embedding for d in resp.data]
        except Exception as e:
            raise LLMError(f"provider {self.name} embed 失败: {e}") from e


class LLMClient:
    """统一 LLM 客户端：管理多个 provider，按 fallback_chain 降级。"""

    def __init__(self) -> None:
        self._providers: dict[str, _Provider] = {}
        for name, cfg in settings.config["llm"]["providers"].items():
            self._providers[name] = _Provider(name, cfg)
        self._chat_chain = self._build_chain(settings.chat_provider, "chat")
        self._embed_chain = self._build_chain(settings.embedding_provider, "embed")

    def _build_chain(self, primary: str, mode: str) -> list[str]:
        """构建 fallback 顺序：primary 在前，其他可用的依次。"""
        chain: list[str] = []
        if primary in self._providers:
            chain.append(primary)
        for name in settings.fallback_chain:
            if name not in chain and name in self._providers:
                chain.append(name)
        # 过滤：chat 模式必须有 chat_model；embed 模式必须有 embedding_model
        filtered: list[str] = []
        for n in chain:
            p = self._providers[n]
            if not p.usable:
                continue
            if mode == "chat" and p.chat_model:
                filtered.append(n)
            elif mode == "embed" and p.embedding_model and p.embedding_model is not False:
                filtered.append(n)
        return filtered

    def _pick(self, chain: list[str]) -> _Provider:
        for name in chain:
            p = self._providers[name]
            if p.usable:
                return p
        raise NoAvailableProviderError(
            f"没有可用的 provider，chain={chain}。"
            "请检查 config.yaml 中 enabled 和 .env 中 API key。"
        )

    @retry(
        retry=retry_if_exception_type(LLMError),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
    )
    def chat(self, messages: list[dict], **kwargs: Any) -> tuple[str, str]:
        """对话。返回 (回答, 使用的 provider 名)。"""
        last_err: Exception | None = None
        for name in self._chat_chain:
            try:
                p = self._providers[name]
                if not p.usable:
                    continue
                answer = p.chat(messages, **kwargs)
                return answer, name
            except LLMError as e:
                last_err = e
                continue
        raise NoAvailableProviderError(f"所有 chat provider 都失败: {last_err}")

    @retry(
        retry=retry_if_exception_type(LLMError),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
    )
    def embed(self, texts: list[str]) -> tuple[list[list[float]], str]:
        """向量化。返回 (向量列表, 使用的 provider 名)。"""
        if not texts:
            return [], ""
        # 单批最多 64 条（OpenAI 兼容 API 常见上限）
        batch_size = 64
        all_vecs: list[list[float]] = []
        used_provider = ""
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            last_err: Exception | None = None
            for name in self._embed_chain:
                try:
                    p = self._providers[name]
                    if not p.usable:
                        continue
                    vecs = p.embed(batch)
                    all_vecs.extend(vecs)
                    used_provider = name
                    break
                except LLMError as e:
                    last_err = e
                    continue
            else:
                raise NoAvailableProviderError(f"所有 embed provider 都失败: {last_err}")
        return all_vecs, used_provider


# ===== 单例 =====
_client: LLMClient | None = None


def get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def reset_client() -> None:
    """测试用：重置单例。"""
    global _client
    _client = None


def health_check() -> dict:
    """健康检查：返回 LLM 配置状态。"""
    cfg = settings.config["llm"]
    providers_status = {}
    for name, p in cfg["providers"].items():
        providers_status[name] = {
            "enabled": p.get("enabled", False),
            "has_chat": p.get("chat_model") is not None,
            "has_embedding": p.get("embedding_model") not in (None, False),
            "api_key_configured": bool(settings.resolve_api_key(p)) or name == "local",
        }
    try:
        client = get_client()
        chat_chain = client._chat_chain
        embed_chain = client._embed_chain
    except Exception as e:
        chat_chain = []
        embed_chain = []
    return {
        "status": "configured",
        "current_chat_provider": cfg["chat_provider"],
        "current_embedding_provider": cfg["embedding_provider"],
        "fallback_chain": cfg["fallback_chain"],
        "effective_chat_chain": chat_chain,
        "effective_embed_chain": embed_chain,
        "providers": providers_status,
    }
