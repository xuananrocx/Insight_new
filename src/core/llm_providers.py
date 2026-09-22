"""LLM Provider 抽象层 - 支持 OpenAI 和 Anthropic 双协议。"""
from __future__ import annotations

import json
import logging
import uuid
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator

import httpx
from openai import AsyncOpenAI, OpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """LLM 调用失败。"""


class BaseLLMProvider(ABC):
    """LLM Provider 抽象基类。"""

    def __init__(self, name: str, cfg: dict[str, Any]) -> None:
        self.name = name
        self.cfg = cfg
        self.base_url = cfg.get("base_url", "")
        self.chat_model = cfg.get("chat_model")
        self.embedding_model = cfg.get("embedding_model")
        # 超时可配置（由 llm_client 从全局 llm 配置注入，缺省保守值）
        self.request_timeout = float(cfg.get("request_timeout_seconds", 120.0))
        self.test_timeout = float(cfg.get("test_timeout_seconds", 10.0))
        # 内部禁用标记（如 SSRF 拦截后置位），不落盘
        self._disabled = bool(cfg.get("_disabled", False))

    @property
    def has_chat_model(self) -> bool:
        return bool(self.chat_model)

    @property
    def has_embedding_model(self) -> bool:
        return bool(self.embedding_model) and self.embedding_model is not False

    @abstractmethod
    def test_connection(self) -> bool:
        """测试连接是否可用。"""
        pass

    @abstractmethod
    def chat(self, messages: list[dict], **kwargs: Any) -> str:
        """同步聊天。"""
        pass

    @abstractmethod
    async def chat_stream(self, messages: list[dict], **kwargs: Any) -> AsyncGenerator[str, None]:
        """流式聊天。"""
        pass

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """文本向量化。"""
        pass

    def close(self, force: bool = False) -> None:
        """释放底层资源（HTTP 连接等）。

        默认空实现；子类如有 httpx.Client / anthropic.Client 等需重写。
        在切换 provider 时被 LLMClient 调用，避免连接泄漏。

        force=True：无视 in-flight 请求直接关闭（用于进程退出/测试清理）。
        """
        pass


def _normalize_openai_base_url(url: str) -> tuple[str, str]:
    """规范化 OpenAI 格式 base_url，自动识别 API 风格。

    支持以下填法：
      https://xxx                     → https://xxx/v1（仅域名自动补标准前缀）
      https://xxx/v1                  → chat（SDK 拼 /chat/completions）
      https://xxx/v1/chat/completions → chat（去掉后缀）
      https://xxx/v1/responses        → responses（Responses API，去掉后缀）
    返回 (规范后的 base_url, api_style)。
    """
    u = (url or "").rstrip("/")
    if u.endswith("/responses"):
        return u[: -len("/responses")], "responses"
    if u.endswith("/chat/completions"):
        return u[: -len("/chat/completions")], "chat"
    # A bare origin is the common UI shorthand for the OpenAI /v1 API.
    # Explicit endpoints above and custom prefixes below remain authoritative.
    if u:
        from urllib.parse import urlsplit
        parsed = urlsplit(u)
        if parsed.scheme in ("http", "https") and parsed.netloc and not parsed.path:
            return parsed._replace(path="/v1").geturl(), "chat"
    return u, "chat"


class OpenAIProvider(BaseLLMProvider):
    """OpenAI 协议 Provider（兼容 DeepSeek/GLM/等）。

    base_url 末尾为 /responses 时自动走 Responses API，否则走 chat/completions。
    """

    def __init__(self, name: str, cfg: dict[str, Any], api_key: str | None = None) -> None:
        super().__init__(name, cfg)
        self._api_key = api_key or cfg.get("api_key", "")
        self.base_url, self._api_style = _normalize_openai_base_url(self.base_url)
        self._client: OpenAI | None = None
        self._async_client: AsyncOpenAI | None = None
        self._test_client: OpenAI | None = None

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    @property
    def usable(self) -> bool:
        return not self._disabled and self.has_api_key

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=self._api_key or "not-required",
                base_url=self.base_url,
                http_client=httpx.Client(timeout=self.request_timeout),
            )
        return self._client

    @property
    def async_client(self) -> AsyncOpenAI:
        if self._async_client is None:
            self._async_client = AsyncOpenAI(
                api_key=self._api_key or "not-required",
                base_url=self.base_url,
                http_client=httpx.AsyncClient(timeout=self.request_timeout),
            )
        return self._async_client

    @property
    def test_client(self) -> OpenAI:
        """连接测试专用客户端（独立短超时）。"""
        if self._test_client is None:
            self._test_client = OpenAI(
                api_key=self._api_key or "not-required",
                base_url=self.base_url,
                http_client=httpx.Client(timeout=self.test_timeout),
            )
        return self._test_client

    def test_connection(self) -> bool:
        """测试连接：发送最小请求验证 API 可用（短超时）。"""
        if not self.has_chat_model:
            raise ValueError(f"provider {self.name} 未配置 chat_model")
        try:
            if self._api_style == "responses":
                resp = self.test_client.responses.create(
                    model=self.chat_model,
                    input="hi",
                    max_output_tokens=16,
                )
                if not (resp.output_text or "").strip():
                    raise ValueError("API 返回空响应")
                return True
            resp = self.test_client.chat.completions.create(
                model=self.chat_model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )
            if not resp.choices:
                raise ValueError("API 返回空响应")
            return True
        except Exception as e:
            logger.warning(f"OpenAI provider {self.name} 连接测试失败: {e}")
            raise  # 重新抛出异常，让上层获取详细错误

    @staticmethod
    def _responses_kwargs(kwargs: dict) -> dict:
        """chat 参数 → Responses API 参数映射。"""
        out = dict(kwargs)
        if "max_tokens" in out:
            out["max_output_tokens"] = out.pop("max_tokens")
        return out

    def chat(self, messages: list[dict], **kwargs: Any) -> str:
        if not self.chat_model:
            raise LLMError(f"provider {self.name} 未配置 chat_model")
        try:
            if self._api_style == "responses":
                resp = self.client.responses.create(
                    model=self.chat_model,
                    input=messages,
                    **self._responses_kwargs(kwargs),
                )
                return resp.output_text or ""
            resp = self.client.chat.completions.create(
                model=self.chat_model,
                messages=messages,
                **kwargs,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            raise LLMError(f"provider {self.name} chat 失败: {e}") from e

    async def chat_stream(self, messages: list[dict], **kwargs: Any) -> AsyncGenerator[str, None]:
        """流式生成，逐 token yield。stream 启动失败时降级为同步一次性返回。"""
        if not self.chat_model:
            raise LLMError(f"provider {self.name} 未配置 chat_model")
        if self._api_style == "responses":
            async for token in self._chat_stream_responses(messages, **kwargs):
                yield token
            return
        try:
            stream = await self.async_client.chat.completions.create(
                model=self.chat_model,
                messages=messages,
                stream=True,
                **kwargs,
            )
        except Exception as e:
            logger.warning(f"provider {self.name} stream 启动失败，降级同步: {e}")
            yield self.chat(messages, **kwargs)
            return

        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content
        except Exception as e:
            raise LLMError(f"provider {self.name} chat_stream 中途失败: {e}") from e
        finally:
            try:
                await stream.close()
            except Exception:
                pass

    async def _chat_stream_responses(self, messages: list[dict], **kwargs: Any) -> AsyncGenerator[str, None]:
        """Responses API 流式：response.output_text.delta 事件携带增量文本。"""
        try:
            stream = await self.async_client.responses.create(
                model=self.chat_model,
                input=messages,
                stream=True,
                **self._responses_kwargs(kwargs),
            )
        except Exception as e:
            logger.warning(f"provider {self.name} stream 启动失败，降级同步: {e}")
            yield self.chat(messages, **kwargs)
            return
        try:
            async for event in stream:
                if event.type == "response.output_text.delta" and event.delta:
                    yield event.delta
        except Exception as e:
            raise LLMError(f"provider {self.name} chat_stream 中途失败: {e}") from e

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.embedding_model:
            raise LLMError(f"provider {self.name} 未配置 embedding_model")
        if self.embedding_model is False:
            raise LLMError(f"provider {self.name} 不支持 embedding")
        if self._api_style == "responses":
            raise LLMError(f"provider {self.name} 走 Responses API 时不支持 embedding")
        try:
            resp = self.client.embeddings.create(
                model=self.embedding_model,
                input=texts,
            )
            return [d.embedding for d in resp.data]
        except Exception as e:
            raise LLMError(f"provider {self.name} embed 失败: {e}") from e

    def close(self, force: bool = False) -> None:
        """释放底层 OpenAI SDK 的 httpx 连接。"""
        for attr in ("_client", "_async_client", "_test_client"):
            client = getattr(self, attr, None)
            if client is not None:
                try:
                    client.close()
                except Exception as e:
                    logger.debug(f"close {attr} 失败: {e}")
                setattr(self, attr, None)


class AnthropicProvider(BaseLLMProvider):
    """Anthropic 协议 Provider（原生 Claude + archforce 等代理）。"""

    def __init__(self, name: str, cfg: dict[str, Any], api_key: str | None = None) -> None:
        super().__init__(name, cfg)
        self._api_key = api_key or cfg.get("api_key", "")
        # Anthropic 默认使用官方端点，支持自定义 BASE_URL（如 archforce）
        self.api_base = self.base_url or "https://api.anthropic.com"
        self._client: Any = None
        self._async_client: Any = None
        self._test_client: Any = None

    def _init_client(self) -> Any:
        """延迟导入 anthropic（仅在需要时加载）。"""
        try:
            import anthropic
        except ImportError:
            raise LLMError("anthropic 包未安装，请运行: pip install anthropic")

        return anthropic.Anthropic(
            api_key=self._api_key or "not-required",
            base_url=self.api_base,
            timeout=self.request_timeout,
        )

    def _init_async_client(self) -> Any:
        """延迟导入 anthropic.AsyncAnthropic。"""
        try:
            import anthropic
        except ImportError:
            raise LLMError("anthropic 包未安装，请运行: pip install anthropic")

        return anthropic.AsyncAnthropic(
            api_key=self._api_key or "not-required",
            base_url=self.api_base,
            timeout=self.request_timeout,
        )

    def _init_test_client(self) -> Any:
        """连接测试专用客户端（独立短超时）。"""
        try:
            import anthropic
        except ImportError:
            raise LLMError("anthropic 包未安装，请运行: pip install anthropic")

        return anthropic.Anthropic(
            api_key=self._api_key or "not-required",
            base_url=self.api_base,
            timeout=self.test_timeout,
        )

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    @property
    def usable(self) -> bool:
        return not self._disabled and self.has_api_key

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self._init_client()
        return self._client

    @property
    def async_client(self) -> Any:
        if self._async_client is None:
            self._async_client = self._init_async_client()
        return self._async_client

    @property
    def test_client(self) -> Any:
        if self._test_client is None:
            self._test_client = self._init_test_client()
        return self._test_client

    def test_connection(self) -> bool:
        """测试连接：发送最小请求验证 API 可用（短超时）。"""
        if not self.has_chat_model:
            raise ValueError(f"provider {self.name} 未配置 chat_model")
        try:
            resp = self.test_client.messages.create(
                model=self.chat_model,
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            if not resp.content:
                raise ValueError("API 返回空响应")
            return True
        except Exception as e:
            logger.warning(f"Anthropic provider {self.name} 连接测试失败: {e}")
            raise  # 重新抛出异常，让上层获取详细错误

    def _convert_to_anthropic_format(
        self, messages: list[dict]
    ) -> tuple[list[dict], str | None]:
        """将 OpenAI 格式消息转换为 Anthropic 格式。

        关键差异：
        - OpenAI: system 是 messages 数组里 role=system 的消息
        - Anthropic: system 是独立参数（不能放在 messages 里）

        返回：(messages_without_system, system_text)
        - messages_without_system: 只含 user/assistant 消息
        - system_text: 多条 system 消息拼成一段文本（多条用 "\n\n" 分隔）
        """
        anthropic_messages: list[dict] = []
        system_parts: list[str] = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                # 收集 system 文本，最后拼成独立参数
                if isinstance(content, str) and content.strip():
                    system_parts.append(content)
                continue

            # tool / function 角色不在基础流程里使用，统一降级为 user/assistant
            if role not in ("user", "assistant"):
                role = "user" if not anthropic_messages else "assistant"

            anthropic_messages.append({"role": role, "content": content})

        # Anthropic 要求第一条消息必须是 user
        if anthropic_messages and anthropic_messages[0]["role"] != "user":
            anthropic_messages.insert(0, {"role": "user", "content": "Continue"})

        system_text = "\n\n".join(system_parts) if system_parts else None
        return anthropic_messages, system_text

    def chat(self, messages: list[dict], **kwargs: Any) -> str:
        if not self.chat_model:
            raise LLMError(f"provider {self.name} 未配置 chat_model")
        try:
            anthropic_messages, system_text = self._convert_to_anthropic_format(messages)

            # 提取 Anthropic 特定参数
            max_tokens = kwargs.pop("max_tokens", 1024)
            temperature = kwargs.pop("temperature", None)
            # 调用方可能显式传了 system（优先级最高）
            if "system" in kwargs:
                system_text = kwargs.pop("system")

            create_kwargs: dict[str, Any] = {
                "model": self.chat_model,
                "max_tokens": max_tokens,
                "messages": anthropic_messages,
            }
            if temperature is not None:
                create_kwargs["temperature"] = temperature
            if system_text:
                create_kwargs["system"] = system_text
            create_kwargs.update(kwargs)

            resp = self.client.messages.create(**create_kwargs)

            # 提取文本内容
            if hasattr(resp, 'content') and resp.content:
                for block in resp.content:
                    if hasattr(block, 'text'):
                        return block.text
            return ""
        except Exception as e:
            raise LLMError(f"provider {self.name} chat 失败: {e}") from e

    async def chat_stream(self, messages: list[dict], **kwargs: Any) -> AsyncGenerator[str, None]:
        """Anthropic 流式生成。

        关键约束：只在「启动失败」时降级同步；「中途失败」让异常冒泡。
        原因：流已经开始 yield 后再 fallback self.chat() 会输出重复 token
        （前半段流式 + 完整同步 = 双倍内容）。
        """
        if not self.chat_model:
            raise LLMError(f"provider {self.name} 未配置 chat_model")

        anthropic_messages, system_text = self._convert_to_anthropic_format(messages)

        # 提取参数
        max_tokens = kwargs.pop("max_tokens", 1024)
        temperature = kwargs.pop("temperature", None)
        if "system" in kwargs:
            system_text = kwargs.pop("system")

        stream_kwargs: dict[str, Any] = {
            "model": self.chat_model,
            "max_tokens": max_tokens,
            "messages": anthropic_messages,
        }
        if temperature is not None:
            stream_kwargs["temperature"] = temperature
        if system_text:
            stream_kwargs["system"] = system_text
        stream_kwargs.update(kwargs)

        # 阶段 1：尝试启动流（启动失败可以 fallback）
        stream_ctx = None
        try:
            stream_ctx = self.async_client.messages.stream(**stream_kwargs)
            stream = stream_ctx.__enter__()
        except Exception as e:
            # __enter__ 失败时显式清理 stream_ctx（避免 TCP 连接泄漏）
            if stream_ctx is not None:
                try:
                    stream_ctx.__exit__(type(e), e, e.__traceback__)
                except Exception as cleanup_err:
                    logger.debug(f"stream_ctx 启动失败 cleanup 异常: {cleanup_err}")
            logger.warning(f"provider {self.name} stream 启动失败，降级同步: {e}")
            # fallback 时显式 strip stream 参数，避免传给同步 chat
            kwargs.pop("stream", None)
            yield self.chat(messages, max_tokens=max_tokens, temperature=temperature, **kwargs)
            return

        # 阶段 2：迭代流（中途失败让异常冒泡，不 fallback）
        try:
            import sys
            try:
                for text in stream.text_stream:
                    yield text
            except BaseException:
                # 让 GeneratorExit/CancelledError 等 BaseException 也走 finally 清理
                raise
        finally:
            try:
                exc_type, exc_val, exc_tb = sys.exc_info()
                if exc_val is None:
                    stream_ctx.__exit__(None, None, None)
                else:
                    stream_ctx.__exit__(exc_type, exc_val, exc_tb)
            except Exception as cleanup_err:
                logger.debug(f"stream cleanup 失败: {cleanup_err}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Anthropic 不提供 embedding API，抛出错误。"""
        raise LLMError(f"provider {self.name} 不支持 embedding（Anthropic 无此 API）")

    def close(self, force: bool = False) -> None:
        """释放底层 anthropic SDK 的 httpx 连接。"""
        for attr in ("_client", "_async_client", "_test_client"):
            client = getattr(self, attr, None)
            if client is not None:
                try:
                    client.close()
                except Exception as e:
                    logger.debug(f"close {attr} 失败: {e}")
                setattr(self, attr, None)


# ==================== Claude Code 客户端伪装常量 ====================
# 参考 Vibe-Trading 项目的 archforce 兼容方案
# archforce 服务端检测客户端身份，必须伪装成 Claude Code CLI 才能通过
_CC_CLI_VERSION = "2.1.97"
_CC_HEADERS = {
    "User-Agent": f"claude-cli/{_CC_CLI_VERSION} (external, cli)",
    "X-App": "cli",
    "X-Stainless-Lang": "js",
    "X-Stainless-Package-Version": "0.70.0",
    "X-Stainless-OS": "Windows",
    "X-Stainless-Arch": "x64",
    "X-Stainless-Runtime": "node",
    "X-Stainless-Runtime-Version": "v22.16.0",
    "Anthropic-Dangerous-Direct-Browser-Access": "true",
}
_CC_BETA = "claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14"
_CC_DEVICE_ID = "a" * 64
_CC_SYSTEM_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."


def _make_cc_metadata_user_id() -> str:
    """构造 Claude Code 格式的 metadata.user_id。"""
    return json.dumps({
        "device_id": _CC_DEVICE_ID,
        "account_uuid": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
    })


def _inject_cc_system_and_metadata(payload: dict) -> None:
    """修改 payload，伪装成 Claude Code 客户端的请求格式。

    - system 字段强制改为数组，首项是 Claude Code 标识
    - 添加 metadata.user_id（含 device_id/account_uuid/session_id）
    """
    existing_system = payload.get("system")
    if isinstance(existing_system, str) and existing_system:
        combined = existing_system
    elif isinstance(existing_system, list):
        combined = "\n".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in existing_system
        )
    else:
        combined = ""

    payload["system"] = [
        {"type": "text", "text": _CC_SYSTEM_PREFIX},
        {"type": "text", "text": combined},
    ]
    payload["metadata"] = {"user_id": _make_cc_metadata_user_id()}


class AnthropicArchProvider(BaseLLMProvider):
    """archforce（伪装 Claude Code）Provider。

    通过伪装 Claude Code CLI 客户端的请求特征绕过服务端检测。
    不使用 anthropic SDK，而是直接走 httpx，完全控制请求格式。
    """

    def __init__(self, name: str, cfg: dict[str, Any], api_key: str | None = None) -> None:
        super().__init__(name, cfg)
        self._api_key = api_key or cfg.get("api_key", "")
        self.api_base = (self.base_url or "https://plan.ai.archforce.cn").rstrip("/")
        # 流式模型可能不同（部分代理区分 stream 模型）
        self._sync_client: httpx.Client | None = None
        self._async_client: httpx.AsyncClient | None = None
        # in-flight 引用计数 + dirty flag（用于 reset_client 时不打断进行中的请求）
        self._inflight = 0
        self._dirty = False

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    @property
    def usable(self) -> bool:
        return not self._disabled and self.has_api_key

    @property
    def sync_client(self) -> httpx.Client:
        if self._sync_client is None:
            self._sync_client = httpx.Client(timeout=self.request_timeout)
        return self._sync_client

    @property
    def async_client(self) -> httpx.AsyncClient:
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=self.request_timeout)
        return self._async_client

    def _build_headers(self) -> dict[str, str]:
        """构造完整的 Claude Code 伪装 headers。"""
        h = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": _CC_BETA,
        }
        h.update(_CC_HEADERS)
        return h

    def _build_payload(self, messages: list[dict], max_tokens: int, temperature: float | None, **kwargs: Any) -> dict:
        """构造请求 payload，注入 Claude Code 伪装字段。"""
        payload: dict[str, Any] = {
            "model": self.chat_model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        # 系统提示（如果用户传了 system）
        system_prompt = kwargs.pop("system", None)
        if system_prompt:
            payload["system"] = system_prompt
        # 任务 ID（6-64 字符）
        payload["id"] = f"msg_{uuid.uuid4().hex[:24]}"
        # 注入 Claude Code 必须字段
        _inject_cc_system_and_metadata(payload)
        return payload

    def test_connection(self) -> bool:
        if not self.has_chat_model:
            raise ValueError(f"provider {self.name} 未配置 chat_model")
        if not self.has_api_key:
            raise ValueError(f"provider {self.name} 未配置 api_key")
        payload = self._build_payload(
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=10,
            temperature=None,
        )
        # 连接测试用独立短超时客户端
        test_client = httpx.Client(timeout=self.test_timeout)
        try:
            resp = test_client.post(
                f"{self.api_base}/v1/messages",
                headers=self._build_headers(),
                json=payload,
            )
            if resp.status_code != 200:
                err_body = resp.text[:500]
                raise ValueError(f"HTTP {resp.status_code}: {err_body}")
            data = resp.json()
            if not data.get("content"):
                raise ValueError("API 返回空响应")
            return True
        except Exception as e:
            logger.warning(f"AnthropicArch provider {self.name} 连接测试失败: {e}")
            raise
        finally:
            test_client.close()

    def chat(self, messages: list[dict], **kwargs: Any) -> str:
        if not self.chat_model:
            raise LLMError(f"provider {self.name} 未配置 chat_model")
        max_tokens = kwargs.pop("max_tokens", 1024)
        temperature = kwargs.pop("temperature", None)
        self._incr_inflight()
        try:
            payload = self._build_payload(messages, max_tokens, temperature, **kwargs)
            resp = self.sync_client.post(
                f"{self.api_base}/v1/messages",
                headers=self._build_headers(),
                json=payload,
            )
            if resp.status_code != 200:
                err_body = resp.text[:500]
                raise LLMError(f"HTTP {resp.status_code}: {err_body}")
            data = resp.json()
            # 提取文本
            for block in data.get("content", []):
                if block.get("type") == "text":
                    return block.get("text", "")
            return ""
        except Exception as e:
            raise LLMError(f"provider {self.name} chat 失败: {e}") from e
        finally:
            self._decr_inflight()

    async def chat_stream(self, messages: list[dict], **kwargs: Any) -> AsyncGenerator[str, None]:
        """流式生成，使用 SSE 协议。"""
        if not self.chat_model:
            raise LLMError(f"provider {self.name} 未配置 chat_model")
        max_tokens = kwargs.pop("max_tokens", 1024)
        temperature = kwargs.pop("temperature", None)

        payload = self._build_payload(messages, max_tokens, temperature, **kwargs)
        payload["stream"] = True

        self._incr_inflight()
        try:
            async with self.async_client.stream(
                "POST",
                f"{self.api_base}/v1/messages",
                headers=self._build_headers(),
                json=payload,
            ) as resp:
                if resp.status_code != 200:
                    err_body = await resp.aread()
                    err_text = err_body.decode("utf-8", errors="replace")[:500]
                    raise LLMError(f"HTTP {resp.status_code}: {err_text}")

                # 解析 SSE 流（data: {...}\n\n 格式）
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    # Anthropic SSE 事件类型
                    event_type = chunk.get("type", "")
                    if event_type == "content_block_delta":
                        delta = chunk.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                yield text
        except Exception as e:
            logger.warning(f"provider {self.name} stream 失败，降级同步: {e}")
            yield self.chat(messages, max_tokens=max_tokens, temperature=temperature)
            return
        finally:
            self._decr_inflight()

    def embed(self, texts: list[str]) -> list[list[float]]:
        """archforce 不支持 embedding API。"""
        raise LLMError(f"provider {self.name} 不支持 embedding")

    def close(self, force: bool = False) -> None:
        """释放 httpx 客户端连接。

        若有 in-flight 请求（_inflight > 0），默认只标记 _dirty，
        由请求结束时的 _decr_inflight 触发真正的 close。
        避免切 provider 时打断进行中的 SSE 流。

        force=True：无视 in-flight 计数直接关闭（用于进程退出/测试清理）。
        注意：force 关闭会中断进行中的 SSE 流，仅在确信无 critical 请求时使用。
        """
        if not force and self._inflight > 0:
            self._dirty = True
            logger.info(
                f"provider {self.name} 有 {self._inflight} 个 in-flight 请求，"
                f"延迟关闭（请求结束后自动释放）"
            )
            return
        self._do_close()

    def _do_close(self) -> None:
        """真正释放连接。"""
        for attr in ("_sync_client", "_async_client"):
            client = getattr(self, attr, None)
            if client is not None:
                try:
                    client.close()
                except Exception as e:
                    logger.debug(f"close {attr} 失败: {e}")
                setattr(self, attr, None)
        self._dirty = False

    def _incr_inflight(self) -> None:
        self._inflight += 1

    def _decr_inflight(self) -> None:
        if self._inflight > 0:
            self._inflight -= 1
        # 引用归零且被标记 dirty，触发延迟关闭
        if self._inflight == 0 and self._dirty:
            self._do_close()
