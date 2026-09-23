"""Default-deny HTTP authentication plus object authorization after routing."""

from __future__ import annotations

import secrets
from fastapi import HTTPException, Request
from starlette.responses import JSONResponse
from src.core import accounts as a, permissions as p

PUBLIC = {"/api/v1/auth/status", "/api/v1/auth/login", "/api/v1/auth/setup"}
SAFE = {"GET", "HEAD", "OPTIONS"}


class AuthenticationMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        path = request.url.path.rstrip("/") or "/"
        protected = path.startswith("/api/") or path in (
            "/docs",
            "/redoc",
            "/openapi.json",
        )
        if not protected:
            return await self.app(scope, receive, send)
        current = a.session_identity(request.cookies.get(a.COOKIE))
        if path not in PUBLIC and not current:
            return await JSONResponse({"detail": "请先登录"}, 401)(scope, receive, send)
        if request.method not in SAFE:
            # Required even on login/setup to prevent login CSRF; same-origin JS sends this.
            if request.headers.get("x-insight-request") != "1":
                return await JSONResponse({"detail": "请求来源校验失败"}, 403)(
                    scope, receive, send
                )
            if path not in PUBLIC and not secrets.compare_digest(
                request.headers.get("x-csrf-token", ""), current["csrf"]
            ):
                return await JSONResponse({"detail": "会话校验失败，请刷新页面"}, 403)(
                    scope, receive, send
                )
        if (
            current
            and current["must_change_password"]
            and path
            not in PUBLIC
            | {"/api/v1/auth/me", "/api/v1/auth/password", "/api/v1/auth/logout"}
        ):
            return await JSONResponse({"detail": "请先修改管理员提供的初始密码"}, 403)(
                scope, receive, send
            )
        token = a.identity.set(current)
        provider_token = a.selected_provider.set(None)
        kb_token = a.active_kb.set(None)
        snapshot_token = a.provider_snapshot.set(None)

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend(
                    [
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                    ]
                )
            await send(message)
            if (
                message["type"] == "http.response.start"
                and 200 <= message["status"] < 300
                and request.method not in SAFE
                and path.startswith(("/api/v1/settings/", "/api/v1/embedding/"))
            ):
                a.audit("system_configuration_changed", request.method + " " + path)

        try:
            await self.app(scope, receive, secure_send)
        finally:
            a.identity.reset(token)
            a.selected_provider.reset(provider_token)
            a.active_kb.reset(kb_token)
            a.provider_snapshot.reset(snapshot_token)


async def authorize(request: Request):
    path = request.url.path.rstrip("/")
    if path in PUBLIC or path == "/health":
        return
    if not (path.startswith("/api/") or path in ("/docs", "/redoc", "/openapi.json")):
        return
    current = a.user()
    method = request.method
    params = request.path_params
    route = request.scope.get("route")
    template = getattr(route, "path", path)
    data = {}
    if method not in SAFE:
        content = request.headers.get("content-type", "")
        if "application/json" in content and await request.body():
            try:
                data = await request.json()
            except ValueError:
                raise HTTPException(400, "JSON 格式不正确")
            if not isinstance(data, dict):
                data = {}  # a few old endpoints accept lists; object IDs still come from path
        elif "multipart/form-data" in content:
            data = dict(await request.form())
    if path.startswith("/api/v1/auth/"):
        return
    if path.startswith(
        ("/api/v1/admin/", "/api/v1/access/", "/api/v1/permissions/", "/api/v1/roles/")
    ):
        return  # Each endpoint requires its specific capability or resource management right.
    if path.startswith("/api/v1/providers") or path in (
        "/api/v1/account/provider",
        "/api/v1/account/preferences",
        "/api/v1/members",
    ):
        return  # endpoints enforce personal ownership / team administration
    if path.startswith("/api/v1/qa/"):
        if params.get("session_id") or data.get("session_id"):
            a.require_owner("session", data.get("session_id") or params["session_id"])
        if path.endswith(("/ask", "/ask_stream")):
            kb = data.get("kb_scope", "default")
            a.require_kb(kb)
            a.active_kb.set(kb)
            sid = data.get("session_id")
            if sid:
                s = a.db.get_session(sid)
                if s.get("kb_scope") != kb:
                    raise HTTPException(400, "会话与提问的知识库不一致")
            mode = data.get("mode") or (
                a.db.get_session(sid).get("retrieval_mode") if sid else "ai"
            )
            if mode in ("ai", "deep_ai"):
                a.bind_provider(data.get("provider_id"))
        elif path.endswith("/summarize-title"):
            a.bind_provider(data.get("provider_id"))
        else:
            raise HTTPException(403, "未授权的操作")
        return
    if path.startswith("/api/v1/sessions"):
        if params.get("session_id"):
            a.require_owner("session", params["session_id"])
            if method == "POST" and template.endswith("/turns"):
                a.require_kb(a.db.get_session(params["session_id"]).get("kb_scope"))
        if template.endswith("/export-batch"):
            for sid in data.get("ids", []):
                a.require_owner("session", sid)
        if data.get("kb_scope"):
            a.require_kb(data["kb_scope"])
        return
    if path.startswith("/api/v1/kbs"):
        kb = params.get("kb_id")
        if kb:
            if "/members" in template:
                p.manage_resource("kb", kb)
            elif template.endswith("/export"):
                p.require_resource("kb", kb, "export")
            elif method == "DELETE" and template.endswith("/{kb_id}"):
                a.require_kb(kb, "owner")
            elif method not in SAFE:
                a.require_kb(kb, "manager")
            else:
                a.require_kb(kb)
            a.active_kb.set(kb)
        elif template not in (
            "/api/v1/kbs",
            "/api/v1/kbs/import",
            "/api/v1/kbs/import_stream",
        ):
            raise HTTPException(403, "未授权的知识库操作")
        if method not in SAFE:
            if not kb:
                p.require(
                    "kb.team_create" if data.get("scope") == "team" else "kb.create"
                )
            a.bind_provider(current["default_provider"], required=False)
        return
    if path.startswith("/api/v1/knowledge"):
        if "/download/" in path:
            p.require_resource(
                "kb", request.query_params.get("kb_id") or "", "download"
            )
            return
        if path.endswith(("/supported-types", "/supported_extensions")):
            return
        if path.endswith("/feed_folder/open"):
            p.require("system.edit")
        if path.endswith("/stats"):
            if not (
                p.has("analysis.view")
                or p.has("documents.view")
                or p.has("feedback.view")
            ):
                p.require("analysis.view")
        elif path.endswith("/files") and method == "GET":
            p.require("documents.view")
        task_id = params.get("task_id") or data.get("task_id")
        if task_id:
            task = a.db.get_upload_task(task_id)
            if not task:
                raise HTTPException(404, "上传任务不存在")
            a.require_owner("task", task_id)
            a.require_kb(task["kb_id"], "editor")
            if data.get("kb_id") and data["kb_id"] != task["kb_id"]:
                raise HTTPException(400, "上传任务与知识库不一致")
            a.active_kb.set(task["kb_id"])
        else:
            kb = data.get("kb_id") or request.query_params.get("kb_id")
            if (
                path.endswith(("/stats", "/files", "/upload_tasks/active"))
                and method == "GET"
                and not kb
            ):
                return  # these list endpoints perform SQL filtering
            kb = kb or (
                "default"
                if path.endswith(("/scan", "/upload", "/feed_folder/open"))
                else None
            )
            a.require_kb(kb, "reader" if method in SAFE else "editor")
            a.active_kb.set(kb)
        if method not in SAFE:
            a.bind_provider(current["default_provider"], required=False)
        return
    if path.startswith("/api/v1/ai_logs"):
        p.require("ai_logs.view")
        if params.get("log_id"):
            a.require_owner("log", params["log_id"])
        if method not in SAFE and path.endswith("/config"):
            p.require("system.edit")
        return  # list/stats/batch delete use owner-filtered SQL
    if path.startswith("/api/v1/feedback"):
        if method in SAFE or params.get("feedback_id"):
            p.require("feedback.view")
        if params.get("feedback_id"):
            a.require_owner("feedback", params["feedback_id"])
        return
    if path.startswith("/api/v1/settings"):
        if path.endswith("/llm_providers") and method == "GET":
            return
        if path.endswith("/llm_providers/switch"):
            return
        if "/llm_providers/" in path:
            raise HTTPException(410, "请使用账号设置中的个人 API / 团队 API 管理")
        if path.endswith("/default_kb") and method == "GET":
            return
        if path.endswith("/log_level") and method in SAFE and p.has("logs.view"):
            return
        p.require("system.view" if method in SAFE else "system.edit")
        if path.endswith("/system_prompt/test"):
            a.require_kb("default")
            a.active_kb.set("default")
            a.bind_provider()
        return
    if path.startswith("/api/v1/logs"):
        p.require("logs.view" if method in SAFE else "logs.clear")
        return
    if path.startswith("/api/v1/embedding/") or path in (
        "/api/info",
        "/api/v1/_debug/llm_chat_source",
    ):
        p.require("system.view" if method in SAFE else "system.edit")
        return
    raise HTTPException(403, "未授权的接口")
