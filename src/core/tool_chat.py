"""Native tool conversations, isolated from the existing text-only chat clients."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

import httpx

from src.core import accounts, ai_call_logger
from src.core.llm_providers import OpenAIProvider, AnthropicArchProvider
from src.core.llm_diagnostics import CallDiagnostics
from src.core.api_retry import network_timeout, retry_after, retry_plan, report_retry


class ToolProtocolError(Exception):
    pass


class ToolHTTPError(ToolProtocolError):
    def __init__(self, message, status, retry_after=0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str | dict


@dataclass
class ToolTurn:
    text: str
    calls: list[ToolCall]
    usage: dict


class ToolChat:
    def __init__(self, provider, system: str, messages: list[dict], *, check_access=lambda: None, meta=None, transport=None):
        self.provider = provider
        self.name = provider.name
        self.system = system
        self.messages = list(messages)
        self.check_access = check_access
        self.meta = meta or {}
        self.style = getattr(provider, "_api_style", "chat") if isinstance(provider, OpenAIProvider) else "anthropic"
        self.http = httpx.AsyncClient(timeout=network_timeout(), transport=transport)
        self.retry_count = 10
        self.deadline = float("inf")
        self.attempt_deadline = float("inf")
        self.on_retry = lambda **kwargs: None
        self.attempt = 1

    def _diagnostic(self, scene):
        diagnostic = CallDiagnostics(self.provider, self.style, scene, self.meta)
        diagnostic.data.update(attempt=self.attempt, max_attempts=self.retry_count + 1)
        return diagnostic

    def _failure(self, diagnostic, exc):
        if isinstance(exc, asyncio.CancelledError):
            timed_out = time.monotonic() >= min(self.deadline, self.attempt_deadline)
            diagnostic.data["failure_kind"] = "timeout" if timed_out else "cancelled"
            if timed_out:
                exc = TimeoutError("模型请求超过时间预算")
        return diagnostic.failure(exc)

    async def _retry(self, exc, attempt):
        plan = retry_plan(exc, attempt, self.retry_count, self.deadline)
        if plan is None:
            return False
        await asyncio.to_thread(self.check_access)
        report_retry(plan, "deep_ai", self.meta, callback=self.on_retry)
        await asyncio.sleep(plan["delay"])
        return True

    def _request(self, tools: list[dict], *, final=False, force=False, max_tokens=4096):
        p = self.provider
        if self.style in ("chat", "responses"):
            url = (p.base_url or "https://api.openai.com/v1").rstrip("/")
            headers = {"Authorization": f"Bearer {p._api_key}"}
            functions = [{"type": "function", "function": t} for t in tools]
            if self.style == "chat":
                payload = {"model": p.chat_model, "messages": [{"role": "system", "content": self.system}, *self.messages], "max_tokens": max_tokens}
                if functions:
                    payload.update(tools=functions, tool_choice="none" if final else "required" if force else "auto")
                return url + "/chat/completions", headers, payload
            payload = {"model": p.chat_model, "instructions": self.system, "input": self.messages,
                       "max_output_tokens": max_tokens, "store": False, "include": ["reasoning.encrypted_content"]}
            if tools:
                payload.update(tools=[{"type": "function", **t} for t in tools], tool_choice="none" if final else "required" if force else "auto")
            return url + "/responses", headers, payload
        if isinstance(p, AnthropicArchProvider):
            payload = p._build_payload(self.messages, max_tokens, None, system=self.system)
            headers = p._build_headers()
        else:
            payload = {"model": p.chat_model, "system": self.system, "messages": self.messages, "max_tokens": max_tokens}
            headers = {"x-api-key": p._api_key, "anthropic-version": "2023-06-01"}
        if tools:
            payload.update(tools=[{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools],
                           tool_choice={"type": "none" if final else "any" if force else "auto"})
        base = p.api_base.rstrip("/")
        url = base if base.endswith("/messages") else base + ("/messages" if base.endswith("/v1") else "/v1/messages")
        return url, headers, payload

    @staticmethod
    def _check_response(response, call_id=""):
        if response.is_error:
            raise ToolHTTPError(f"模型请求收到 HTTP {response.status_code}，请查看 AI 调用日志中的错误详情（调用编号：{call_id}）。", response.status_code, retry_after(response.headers))

    def _decode(self, data: dict) -> ToolTurn:
        if data.get("error"):
            raise ToolProtocolError("模型返回错误，未完成工具调用。请检查 API 配置或使用 AI 增强。")
        calls, text = [], ""
        if self.style == "chat":
            choice = data["choices"][0]
            if choice.get("finish_reason") not in ("stop", "tool_calls"):
                raise ToolProtocolError("模型响应不完整，工具尚未执行。请调整模型输出限制。")
            msg = choice["message"]
            for c in msg.get("tool_calls") or []:
                calls.append(ToolCall(c["id"], c["function"]["name"], c["function"]["arguments"]))
            text = msg.get("content") or ""
            # Preserve reasoning_content for providers which require it on subsequent turns.
            self.messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls", "reasoning_content")})
        elif self.style == "responses":
            if data.get("status") != "completed":
                raise ToolProtocolError("模型响应不完整，工具尚未执行。")
            output = data.get("output", [])
            for item in output:
                if item["type"] == "function_call":
                    calls.append(ToolCall(item["call_id"], item["name"], item["arguments"]))
                elif item["type"] == "message":
                    text += "".join(c.get("text", "") for c in item.get("content", []) if c["type"] == "output_text")
            # Includes encrypted reasoning items needed for stateless reasoning models.
            self.messages.extend(output)
        else:
            if data.get("stop_reason") not in ("end_turn", "tool_use", "stop_sequence"):
                raise ToolProtocolError("模型响应不完整，工具尚未执行。")
            blocks = data.get("content", [])
            for b in blocks:
                if b["type"] == "tool_use":
                    calls.append(ToolCall(b["id"], b["name"], b["input"]))
                elif b["type"] == "text":
                    text += b["text"]
            self.messages.append({"role": "assistant", "content": blocks})
        if len({c.id for c in calls}) != len(calls) or any(not c.id for c in calls):
            raise ToolProtocolError("模型返回重复或缺失的工具调用编号。")
        return ToolTurn(text, calls, data.get("usage") or {})

    def _log(self, scene, started, response=None, error=None):
        # Reasoning blocks remain protocol state only, never a displayed thinking trace.
        messages = []
        for message in self.messages:
            if message.get("type") == "reasoning":
                continue
            safe = {k: v for k, v in message.items() if k != "reasoning_content"}
            if isinstance(safe.get("content"), list):
                safe["content"] = [b for b in safe["content"] if b.get("type") not in ("thinking", "redacted_thinking")]
            messages.append(safe)
        ai_call_logger.log_call(provider=self.name, model=self.provider.chat_model, scene=scene,
                                messages=messages, system_prompt=self.system, response_text=response,
                                success=error is None, error_message=error,
                                duration_ms=int((time.monotonic() - started) * 1000), **self.meta)

    async def turn(self, tools, *, force=False, max_tokens=4096):
        for attempt in range(self.retry_count + 1):
            self.attempt = attempt + 1
            self.attempt_deadline = min(self.deadline, time.monotonic() + 45)
            try:
                async with asyncio.timeout(max(0, self.attempt_deadline - time.monotonic())):
                    return await self._turn_once(tools, force=force, max_tokens=max_tokens)
            except (ToolProtocolError, httpx.HTTPError, TimeoutError) as exc:
                if not await self._retry(exc, attempt):
                    raise

    async def _turn_once(self, tools, *, force=False, max_tokens=4096):
        started = time.monotonic()
        diagnostic = self._diagnostic("deep_ai_tools")
        try:
            await asyncio.to_thread(self.check_access)
            diagnostic.data["phase"] = "构造请求"
            url, headers, payload = self._request(tools, force=force, max_tokens=max_tokens)
            diagnostic.request(url, payload)
            response = await self.http.post(url, headers=headers, json=payload, extensions={"trace": diagnostic.trace})
            await diagnostic.response(response)
            self._check_response(response, diagnostic.data["call_id"])
            diagnostic.data["phase"] = "响应后权限检查"
            await asyncio.to_thread(self.check_access)
            diagnostic.data["phase"] = "解析工具响应"
            try:
                result = self._decode(response.json())
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                raise ToolProtocolError("模型未返回完整的原生工具协议响应，请检测工具调用支持或使用 AI 增强。") from exc
            self._log("deep_ai_tools", started, json.dumps({"text": result.text, "tools": [c.name for c in result.calls], "usage": result.usage}, ensure_ascii=False))
            diagnostic.success()
            return result
        except BaseException as exc:
            self._log("deep_ai_tools", started, error=self._failure(diagnostic, exc))
            raise

    def results(self, results: list[tuple[ToolCall, dict]]):
        self.check_access()
        blocks = []
        for call, value in results:
            content = json.dumps(value, ensure_ascii=False)
            if self.style == "chat":
                self.messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
            elif self.style == "responses":
                self.messages.append({"type": "function_call_output", "call_id": call.id, "output": content})
            else:
                blocks.append({"type": "tool_result", "tool_use_id": call.id, "content": content, "is_error": bool(value.get("error"))})
        if blocks:
            self.messages.append({"role": "user", "content": blocks})

    async def final_stream(self, tools, instruction: str, *, max_tokens=4096):
        self.messages.append({"role": "user", "content": instruction})
        for attempt in range(self.retry_count + 1):
            self.attempt = attempt + 1
            self.attempt_deadline = self.deadline
            emitted = False
            try:
                async for token in self._final_once(tools, max_tokens=max_tokens):
                    emitted = True
                    yield token
                return
            except (ToolProtocolError, httpx.HTTPError, TimeoutError) as exc:
                if emitted or not await self._retry(exc, attempt):
                    raise

    async def _final_once(self, tools, *, max_tokens=4096):
        started, parts, complete = time.monotonic(), [], False
        diagnostic = self._diagnostic("deep_ai_answer")
        try:
            await asyncio.to_thread(self.check_access)
            diagnostic.data["phase"] = "构造请求"
            url, headers, payload = self._request(tools, final=True, max_tokens=max_tokens)
            payload["stream"] = True
            diagnostic.request(url, payload)
            async with self.http.stream("POST", url, headers=headers, json=payload, extensions={"trace": diagnostic.trace}) as response:
                await diagnostic.response(response)
                self._check_response(response, diagnostic.data["call_id"])
                diagnostic.data["phase"] = "读取流式回答"
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    event = json.loads(raw)
                    if event.get("error") or event.get("type") == "error":
                        from src.core.llm_diagnostics import error_body
                        diagnostic.data["response_error"] = error_body(raw.encode("utf-8"), self.provider._api_key)
                        raise ToolProtocolError("模型生成中断。")
                    token = ""
                    if self.style == "chat":
                        for choice in event.get("choices", []):
                            if choice.get("finish_reason") == "stop":
                                complete = True
                            token += choice.get("delta", {}).get("content") or ""
                    elif self.style == "responses":
                        if event.get("type") == "response.output_text.delta":
                            token = event.get("delta", "")
                        if event.get("type") == "response.completed":
                            complete = True
                    else:
                        if event.get("type") == "content_block_delta" and event.get("delta", {}).get("type") == "text_delta":
                            token = event["delta"]["text"]
                        if event.get("type") == "message_delta" and event.get("delta", {}).get("stop_reason") in ("end_turn", "stop_sequence"):
                            complete = True
                    if token:
                        parts.append(token)
                        yield token
            if not complete:
                raise httpx.RemoteProtocolError("模型输出未完整结束。")
            await asyncio.to_thread(self.check_access)
            self._log("deep_ai_answer", started, "".join(parts))
            diagnostic.success()
        except BaseException as exc:
            self._log("deep_ai_answer", started, "".join(parts), self._failure(diagnostic, exc))
            raise

    async def close(self):
        await self.http.aclose()


def create_tool_chat(system, messages, *, meta=None):
    if accounts.enabled:
        from src.core.account_llm import chat_client
        client = chat_client()
        provider = next(iter(client._providers.values()))
        check = client.check_access
    else:
        from src.core.llm_client import get_client, NoAvailableProviderError
        client = get_client()
        available = [client._providers[n] for n in client._chat_chain if n in client._providers and client._providers[n].usable]
        if not available:
            raise NoAvailableProviderError("没有可用的 AI API")
        provider = getattr(available[0], "_impl", available[0])
        check = lambda: None
    return ToolChat(provider, system, messages, check_access=check, meta=meta)


async def probe_tools():
    """A real, harmless round trip. No knowledge-base content is sent."""
    import secrets
    from src.qa.knowledge_tools import definition
    tool = definition("insight_connection_probe", "Return a random verification marker. Call with an empty object.")
    model = create_tool_chat("Call the provided tool once, then return its marker exactly. Do not invent the marker.",
                             [{"role": "user", "content": "Verify tool calling now."}])
    model.retry_count = 1
    model.deadline = time.monotonic() + 60
    try:
        turn = await model.turn([tool], force=True, max_tokens=1024)
        if len(turn.calls) != 1 or turn.calls[0].name != tool["name"]:
            return {"supported": False, "message": "未返回预期的原生工具调用，请使用 AI 增强或更换支持工具调用的模型。"}
        args = turn.calls[0].arguments
        if (json.loads(args) if isinstance(args, str) else args) != {}:
            return {"supported": False, "message": "模型返回的工具参数不符合协议。"}
        marker = secrets.token_hex(12)
        model.results([(turn.calls[0], {"marker": marker})])
        text = "".join([part async for part in model.final_stream([tool], "Return only the tool's marker.", max_tokens=1024)])
        ok = marker in text
        return {"supported": ok, "message": "工具调用及结果回传检测通过，可使用新版深度 AI。" if ok else "工具请求成功，但模型未正确读取返回结果；建议使用 AI 增强。"}
    finally:
        await model.close()
