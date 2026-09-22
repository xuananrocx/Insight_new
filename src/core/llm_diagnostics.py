"""Bounded, redacted HTTP diagnostics for native tool calls; never log request bodies."""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import getproxies

from src.core import accounts

logger = logging.getLogger(__name__)
BODY_LIMIT = 8192


def safe_url(value):
    try:
        u = urlsplit(str(value))
        host = u.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if u.port:
            host += f":{u.port}"
        return urlunsplit((u.scheme, host, u.path, "[redacted]" if u.query else "", ""))
    except ValueError:
        return "[invalid URL]"


def redact(value, secret="", limit=4000):
    text = str(value)
    if secret:
        for variant in {secret, quote(secret, safe=""), json.dumps(secret)[1:-1]}:
            text = text.replace(variant, "[redacted]")
    text = re.sub(r"(?:https?|socks5h?)://[^\s\"'<>]+", lambda m: safe_url(m[0]), text)
    text = re.sub(r"(?i)\bBearer\s+[^\s\"',;<>]+", "Bearer [redacted]", text)
    text = re.sub(r'''(?ix)(["']?(?:api[_-]?key|x-api-key|authorization|proxy-authorization|password|secret|access[_-]?token|refresh[_-]?token|cookie|set-cookie)["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\r\n,;}]+)''', r"\1[redacted]", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", text)
    return text[:limit] + ("…[truncated]" if len(text) > limit else "")


def error_body(raw, secret):
    text = raw.decode("utf-8", errors="replace")
    try:
        value = json.loads(text)
        error = value.get("error", value) if isinstance(value, dict) else value
        if isinstance(error, dict):
            selected = {k: error[k] for k in ("message", "type", "code", "param", "detail", "error_description")
                        if k in error and isinstance(error[k], (str, int, float, bool, type(None)))}
            text = json.dumps(selected, ensure_ascii=False) if selected else "JSON 错误响应未包含标准错误字段"
        elif isinstance(error, str):
            text = error
        else:
            text = "非标准 JSON 错误响应"
    except ValueError:
        pass
    return redact(text, secret)


class CallDiagnostics:
    def __init__(self, provider, style, scene, meta):
        self.started = time.monotonic()
        self.secret = provider._api_key
        current = accounts.identity.get() or {}
        self.data = {"call_id": uuid.uuid4().hex[:16], "scene": scene,
                     "user": current.get("username"), "user_id": current.get("id"),
                     "provider_id": accounts.selected_provider.get(), "provider": provider.name,
                     "model": provider.chat_model, "protocol": style, **meta,
                     "phase": "权限检查", "http_status": None}

    def emit(self, event, *, error=False):
        data = {**self.data, "event": event, "duration_ms": int((time.monotonic() - self.started) * 1000)}
        message = json.dumps(self.clean(data), ensure_ascii=False)
        (logger.warning if error else logger.info)("AI HTTP %s", message)

    def clean(self, value):
        if isinstance(value, dict):
            return {k: self.clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        return redact(value, self.secret) if isinstance(value, str) else value

    def request(self, url, payload):
        proxies = getproxies()
        self.data.update(endpoint=safe_url(url), method="POST", stream=bool(payload.get("stream")),
                         request_fields=sorted(payload), tool_count=len(payload.get("tools", [])),
                         tool_choice=payload.get("tool_choice"),
                         max_output_tokens=payload.get("max_output_tokens", payload.get("max_tokens")),
                         message_count=len(payload.get("messages", payload.get("input", []))),
                         request_bytes=len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
                         trust_env=True, environment_proxies={k: safe_url(v) for k, v in proxies.items() if k in ("http", "https", "all")},
                         no_proxy_configured=bool(proxies.get("no")), phase="发起 HTTP 请求")
        self.emit("request_start")

    async def trace(self, event, info):
        # HTTPX/httpcore's trace metadata distinguishes connection, sending and receiving.
        # Never dump info: it can contain headers, request bodies and TLS objects.
        self.data["transport_event"] = event
        if event.endswith("connect_tcp.started"):
            host = info.get("host", "")
            self.data["connect_host"] = host.decode(errors="replace") if isinstance(host, bytes) else str(host)
            self.data["connect_port"] = info.get("port")
            self.emit("connect_start")
        elif event.endswith("send_request_headers.complete"):
            self.emit("request_headers_sent")

    async def response(self, response):
        self.data.update(phase="收到 HTTP 响应", http_status=response.status_code,
                         response_headers={k: response.headers[k] for k in
                                           ("content-type", "content-length", "server", "via", "x-request-id", "request-id", "cf-ray", "retry-after", "retry-after-ms") if k in response.headers})
        stream = response.extensions.get("network_stream")
        if stream:
            try:
                self.data["peer"] = str(stream.get_extra_info("server_addr"))
            except Exception:
                pass
        if response.is_error:
            raw = bytearray()
            if response.is_stream_consumed:
                raw.extend(response.content[:BODY_LIMIT])
                truncated = len(response.content) > BODY_LIMIT
            else:
                truncated = False
                try:
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk[:BODY_LIMIT - len(raw)])
                        if len(raw) >= BODY_LIMIT:
                            truncated = True
                            break
                except Exception as exc:
                    self.data["body_read_error"] = type(exc).__name__
            self.data["response_error"] = error_body(bytes(raw), self.secret)
            self.data["response_body_truncated"] = truncated
        self.emit("response_received", error=response.is_error)

    def failure(self, exc):
        chain, seen = [], set()
        current = exc
        while current is not None and id(current) not in seen and len(chain) < 5:
            seen.add(id(current))
            chain.append({"type": type(current).__name__, "message": redact(str(current), self.secret, 1500)})
            current = current.__cause__ or current.__context__
        self.data["exception_chain"] = chain
        self.emit("request_failed", error=True)
        return json.dumps(self.clean(self.data), ensure_ascii=False, indent=2)

    def success(self):
        self.data["phase"] = "完成"
        self.emit("request_complete")
