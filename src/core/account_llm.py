"""A request's chosen chat provider, separate from the system embedder."""

from __future__ import annotations

from src.core import accounts as a
from src.core.llm_client import LLMClient
from src.core.llm_providers import (
    OpenAIProvider,
    AnthropicProvider,
    AnthropicArchProvider,
)


class AccountChatClient(LLMClient):
    def __init__(self, provider: dict):
        self._account_scoped = True
        self.provider_id = provider["id"]
        self.model_id = provider["chat_model"]
        cfg = a.provider_config(provider)
        cls = {
            "openai": OpenAIProvider,
            "anthropic": AnthropicProvider,
            "anthropic-arch": AnthropicArchProvider,
        }[provider["protocol"]]
        label = f"{provider['name']} · {provider['chat_model']}"
        impl = cls(label, cfg, cfg["api_key"])
        self._providers = {label: impl}
        self._chat_chain = [label]

    def check_access(self):
        a.resolve_provider(self.provider_id, self.model_id)
        if a.active_kb.get():
            a.require_kb(a.active_kb.get())

    def chat(self, *args, **kwargs):
        self.check_access()
        try:
            result = super().chat(*args, **kwargs)
            self.check_access()
            return result
        finally:
            self.close()

    async def chat_stream(self, *args, **kwargs):
        import asyncio
        from contextlib import aclosing
        await asyncio.to_thread(self.check_access)
        try:
            async with aclosing(super().chat_stream(*args, **kwargs)) as stream:
                async for token in stream:
                    await asyncio.to_thread(self.check_access)
                    yield token
        finally:
            for p in self._providers.values():
                client = getattr(p, "_async_client", None)
                if client is not None:
                    close = getattr(client, "aclose", None) or client.close
                    await close()
                    p._async_client = None
            self.close()

    def close(self):
        for p in self._providers.values():
            for attr in ("_client", "_test_client"):
                client = getattr(p, attr, None)
                if client is not None and hasattr(client, "close"):
                    client.close()
                    setattr(p, attr, None)


def chat_client() -> AccountChatClient:
    frozen = a.provider_snapshot.get()
    current = a.resolve_provider(a.selected_provider.get(), frozen["chat_model"] if frozen else None)
    return AccountChatClient(
        frozen if frozen and frozen["id"] == current["id"] else current
    )
