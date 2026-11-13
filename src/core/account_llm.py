"""A request's chosen chat provider, separate from the system embedder."""

from __future__ import annotations

from src.core import accounts as a
from src.core import model_budget
from src.core.llm_client import LLMClient
from src.core.llm_providers import (
    OpenAIProvider,
    AnthropicProvider,
    AnthropicArchProvider,
)


class AccountChatClient(LLMClient):
    def __init__(self, provider: dict):
        self.budget = model_budget.active.get() or model_budget.capture(provider)
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
        impl.web_search_mode = provider.get("web_search", "off")
        self._providers = {label: impl}
        self._chat_chain = [label]

    def check_access(self):
        a.resolve_provider(self.provider_id, self.model_id)
        if a.active_kb.get():
            a.require_kb(a.active_kb.get())

    def chat(self, messages, **kwargs):
        self.check_access()
        try:
            result = super().chat(messages, **model_budget.limit_messages(messages, kwargs, self.budget))
            self.check_access()
            return result
        finally:
            self.close()

    async def chat_stream(self, messages, **kwargs):
        import asyncio
        from contextlib import aclosing
        await asyncio.to_thread(self.check_access)
        try:
            async with aclosing(super().chat_stream(messages, **model_budget.limit_messages(messages, kwargs, self.budget))) as stream:
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
