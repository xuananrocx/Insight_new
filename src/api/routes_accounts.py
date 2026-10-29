"""Account and provider management; no public registration endpoint."""

from __future__ import annotations

import hashlib
import time
import uuid
from urllib.parse import urlsplit
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator
from src.core import accounts as a, permissions as perm, api_models, provider_edits

router = APIRouter(prefix="/api/v1", tags=["accounts"])


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_.@-]+$")
    password: str = Field(min_length=1, max_length=128)


class Setup(Credentials):
    token: str = Field(max_length=200)


@router.get("/auth/status")
def status():
    return {
        "setup_required": not bool(a.rows("SELECT 1 FROM auth_users LIMIT 1")),
        "registration_enabled": False,
    }


@router.post("/auth/setup")
def setup(req: Setup, request: Request):
    a.rate_limit("setup:" + request.client.host, 10)
    a.bootstrap(req.username, req.password, req.token)
    return {"ok": True}


@router.post("/auth/login")
def login(req: Credentials, request: Request, response: Response):
    a.rate_limit("login-ip:" + request.client.host, 50)
    a.rate_limit("login-user:" + req.username.casefold(), 12)
    token, u = a.login(req.username, req.password)
    a.execute(
        "DELETE FROM auth_limits WHERE bucket=?",
        ("login-user:" + req.username.casefold(),),
    )
    response.set_cookie(
        a.COOKIE,
        token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        max_age=a.SESSION_SECONDS,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return u


@router.get("/auth/me")
def me():
    return {**a.public_user(a.user()), "csrf": a.identity.get()["csrf"]}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(a.COOKIE, "")
    a.execute(
        "DELETE FROM auth_sessions WHERE token_hash=?",
        (hashlib.sha256(token.encode()).hexdigest(),),
    )
    response.delete_cookie(a.COOKIE, path="/")
    return {"ok": True}


class PasswordChange(BaseModel):
    old_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)


@router.post("/auth/password")
def change_password(req: PasswordChange, response: Response):
    u = a.user()
    a.rate_limit("password:" + u["id"], 10)
    if not a.check_password(u["password_hash"], req.old_password):
        raise HTTPException(400, "原密码不正确")
    a.validate_password(req.new_password)
    if req.new_password == req.old_password:
        raise HTTPException(400, "新密码不能与原密码相同")
    with a.db.get_cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            "UPDATE auth_users SET password_hash=?,must_change_password=0 WHERE id=?",
            (a.passwords.hash(req.new_password), u["id"]),
        )
        cur.execute("DELETE FROM auth_sessions WHERE user_id=?", (u["id"],))
        cur.connection.commit()
    response.delete_cookie(a.COOKIE, path="/")
    a.audit("password_changed", u["id"])
    return {"ok": True}


@router.get("/members")
def member_directory():
    # Names only, for sharing; no password/session/provider/account detail.
    return perm.visible_members()


@router.get("/kbs/{kb_id}/members")
def kb_members(kb_id: str):
    perm.manage_resource("kb", kb_id)
    users = perm.visible_members()
    return [
        {
            **u,
            "role": next(
                (
                    r
                    for r, action in [
                        ("owner", "owner"),
                        ("manager", "manage"),
                        ("editor", "edit"),
                        ("reader", "query"),
                    ]
                    if (r == "owner" and perm.resource("kb", kb_id, u["id"])["owner"])
                    or action in perm.resource("kb", kb_id, u["id"])["actions"]
                ),
                None,
            ),
        }
        for u in users
        if perm.resource("kb", kb_id, u["id"])["actions"]
    ]


class Grant(BaseModel):
    role: Literal["reader", "editor", "manager"]


@router.put("/kbs/{kb_id}/members/{uid}")
def grant_kb(kb_id: str, uid: str, req: Grant):
    perm.write_grant(
        "kb",
        kb_id,
        dict(
            subject_type="user",
            subject_id=uid,
            effect="allow",
            actions=perm.LEVELS[req.role],
        ),
    )
    return {"ok": True}


@router.delete("/kbs/{kb_id}/members/{uid}")
def revoke_kb(kb_id: str, uid: str):
    perm.write_grant(
        "kb",
        kb_id,
        dict(subject_type="user", subject_id=uid, effect="remove", actions=[]),
    )
    return {"ok": True}


class ModelInput(BaseModel):
    original_model_id: str | None = Field(None, min_length=1, max_length=200)
    model_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field("", max_length=80)
    context_tokens: int | None = Field(None, ge=4096, le=10000000)
    output_tokens: int | None = Field(None, ge=256, le=1000000)

    @model_validator(mode="after")
    def normalize(self):
        self.model_id = self.model_id.strip()
        if self.original_model_id is not None:
            self.original_model_id = self.original_model_id.strip()
            if not self.original_model_id:
                raise ValueError("原模型 ID 不能为空")
        self.display_name = self.display_name.strip()
        if not self.model_id:
            raise ValueError("模型 ID 不能为空")
        return self


class ProviderInput(BaseModel):
    edit_version: str | None = Field(None, pattern=r'^[0-9a-f]{64}$')
    confirmation_token: str | None = Field(None, pattern=r'^[0-9a-f]{64}$')
    name: str = Field(min_length=1, max_length=80)
    scope: Literal["personal", "team"] = "personal"
    protocol: Literal["openai", "anthropic", "anthropic-arch"] = "openai"
    base_url: str = Field(min_length=1, max_length=2048)
    chat_model: str | None = Field(None, min_length=1, max_length=200)
    models: list[ModelInput] | None = Field(None, min_length=1, max_length=100)
    api_key: str | None = Field(None, max_length=8192)
    enabled: bool = True


def manageable(pid: str) -> dict:
    found = a.rows("SELECT * FROM auth_providers WHERE id=?", (pid,))
    if not found:
        raise HTTPException(404, "API 配置不存在")
    p = found[0]
    if p["scope"] == "team":
        perm.require("api.manage")
    elif not perm.has("api.personal") or p["owner_id"] != a.user()["id"]:
        raise HTTPException(404, "API 配置不存在")
    return p


@router.get("/providers")
def providers():
    u = a.user()
    items = []
    for provider in a.rows(
        "SELECT * FROM auth_providers WHERE scope='team' OR owner_id=? ORDER BY scope,name",
        (u["id"],),
    ):
        personal = provider["scope"] == "personal"
        usable = bool(provider["enabled"]) and (
            perm.has("api.personal")
            if personal
            else "use" in perm.resource("api", provider["id"])["actions"]
        )
        if personal and not perm.has("api.personal"):
            continue
        if not personal and not (usable or perm.has("api.view")):
            continue
        if perm.has("api.personal" if personal else "api.manage"):
            with a.db.get_cursor() as cur:
                provider['edit_version'] = provider_edits.edit_version(cur, provider)
        provider["has_api_key"] = bool(
            a.cipher().decrypt(provider.pop("secret").encode())
        )
        provider["models"] = api_models.models(provider)
        provider.update(
            usable=usable,
            manageable=perm.has("api.personal" if personal else "api.manage"),
            grantable=not personal and perm.has("api.grant"),
        )
        items.append(provider)
    return {"items": items, "default_provider": u["default_provider"], "selection": api_models.selection_status(api_models.preferences())}


def save_provider(req: ProviderInput, pid: str | None = None, *, preview=False):
    u = a.user()
    old = manageable(pid) if pid else None
    if req.scope == "team":
        perm.require("api.manage")
    else:
        perm.require("api.personal")
    if old and old["scope"] != req.scope:
        raise HTTPException(400, "个人 API 和团队 API 不能相互转换，请另建配置")
    try:
        url = urlsplit(req.base_url)
        if (
            url.scheme not in ("https", "http")
            or not url.hostname
            or url.username
            or url.password
            or url.fragment
            or url.query
        ):
            raise ValueError()
        _ = url.port
    except ValueError:
        raise HTTPException(400, "请输入合法的 HTTP(S) API 地址，不要在地址中包含密钥")
    # No domain allowlist; public/team and personal configurations use the same rule.
    if req.api_key is None and old:
        secret = old["secret"]
    else:
        secret = a.cipher().encrypt((req.api_key or "").encode()).decode()
    catalog = req.models
    if catalog is None:
        # Legacy updates preserve a multi-model catalog; they may only name the
        # explicit compatibility model, never silently erase the other models.
        if old:
            catalog = [ModelInput(**m) for m in api_models.models(old)]
            if req.chat_model and req.chat_model != old["chat_model"]:
                if len(catalog) > 1:
                    raise HTTPException(409, "请使用新版模型管理修改多模型配置")
                catalog = [ModelInput(model_id=req.chat_model)]
        elif req.chat_model:
            catalog = [ModelInput(model_id=req.chat_model)]
        else:
            raise HTTPException(422, "请至少添加一个模型")
    ids = [m.model_id for m in catalog]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "同一个 API 内的模型 ID 不能重复")
    compatibility_model = (req.chat_model if req.models is None and req.chat_model else old["chat_model"] if old else ids[0])
    pid = pid or uuid.uuid4().hex
    with a.db.get_cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        renames = {}
        if old:
            latest = cur.execute('SELECT * FROM auth_providers WHERE id=?', (pid,)).fetchone()
            if latest is None or dict(latest) != old:
                raise HTTPException(409, 'API 配置已变化，请重新打开编辑窗口')
            if req.edit_version and req.edit_version != provider_edits.edit_version(cur, old):
                raise HTTPException(409, 'API 或模型配置已变化，请重新打开编辑窗口')
            renames, impact, confirmation = provider_edits.plan(cur, old, req, catalog)
            if preview:
                return {**impact, 'confirmation_token': confirmation}
            if renames and not req.edit_version:
                raise HTTPException(409, '修改模型 ID 前请重新打开编辑窗口并确认影响范围')
            if (renames or (req.edit_version and impact['deletions'])) and req.confirmation_token != confirmation:
                raise HTTPException(409, '模型修改的影响范围已变化，请重新预览并确认后保存')
            compatibility_model = renames.get(compatibility_model, compatibility_model)
        elif any(m.original_model_id is not None for m in catalog):
            raise HTTPException(422, '新增 API 不能指定原模型 ID')
        if old:
            cur.execute("""INSERT OR IGNORE INTO api_preferences(user_id,model_id)
              SELECT id,? FROM auth_users WHERE default_provider=?""", (old["chat_model"], pid))
            cur.execute("UPDATE auth_providers SET name=?,protocol=?,base_url=?,chat_model=?,secret=?,enabled=? WHERE id=?",
                        (req.name, req.protocol, req.base_url.strip().rstrip("/"), compatibility_model, secret, int(req.enabled), pid))
        else:
            cur.execute("INSERT INTO auth_providers VALUES (?,?,?,?,?,?,?,?,?)",
                        (pid, u["id"] if req.scope == "personal" else None, req.scope, req.name, req.protocol,
                         req.base_url.strip().rstrip("/"), compatibility_model, secret, int(req.enabled)))
        for model in catalog:
            cur.execute("""INSERT INTO api_models(provider_id,model_id,display_name,revision) VALUES (?,?,?,?)
              ON CONFLICT(provider_id,model_id) DO UPDATE SET display_name=excluded.display_name""",
                        (pid, model.model_id, model.display_name, time.time_ns()))
            if model.original_model_id in renames or 'context_tokens' in model.model_fields_set or 'output_tokens' in model.model_fields_set:
                previous = cur.execute('SELECT context_tokens,output_tokens FROM api_model_limits WHERE provider_id=? AND model_id=?', (pid, model.original_model_id if model.original_model_id in renames else model.model_id)).fetchone()
                context = model.context_tokens if 'context_tokens' in model.model_fields_set else (previous[0] if previous else None)
                output = model.output_tokens if 'output_tokens' in model.model_fields_set else (previous[1] if previous else None)
                if context and output and output >= context:
                    raise HTTPException(422, '模型输出上限必须小于上下文容量')
                cur.execute('INSERT OR REPLACE INTO api_model_limits VALUES (?,?,?,?)', (pid, model.model_id, context, output))

        if old:
            # Materialize implicit compatibility selections before changing their identity.
            for source, target in renames.items():
                if source == old['chat_model']:
                    cur.execute("UPDATE api_preferences SET model_id=? WHERE (model_id IS NULL OR model_id='') AND user_id IN (SELECT id FROM auth_users WHERE default_provider=?)", (source, pid))
                    cur.execute("UPDATE api_session_selection SET model_id=? WHERE provider_id=? AND (model_id IS NULL OR model_id='')", (source, pid))
            provider_edits.migrate_selections(cur, pid, renames)
        placeholders = ",".join("?" for _ in ids)
        cur.execute(f"DELETE FROM api_models WHERE provider_id=? AND model_id NOT IN ({placeholders})", (pid, *ids))

        if old and any((old["protocol"] != req.protocol, old["base_url"] != req.base_url.strip().rstrip("/"), req.api_key is not None)):
            cur.execute("""UPDATE api_models SET revision=revision+1,connection_status=NULL,connection_message=NULL,connection_checked_at=NULL,
              tools_status=NULL,tools_message=NULL,tools_checked_at=NULL WHERE provider_id=?""", (pid,))
        perm.bump(cur)
        cur.connection.commit()
    a.audit("provider_saved", pid)
    return {"id": pid}


@router.post("/providers", status_code=201)
def create_provider(req: ProviderInput):
    return save_provider(req)


@router.put("/providers/{pid}")
def update_provider(pid: str, req: ProviderInput):
    return save_provider(req, pid)


@router.post("/providers/{pid}/edit-preview")
def preview_provider_edit(pid: str, req: ProviderInput):
    return save_provider(req, pid, preview=True)


@router.delete("/providers/{pid}")
def delete_provider(pid: str):
    manageable(pid)
    a.execute("DELETE FROM auth_providers WHERE id=?", (pid,))
    a.execute(
        "DELETE FROM permission_grants WHERE kind='api' AND resource_id=?", (pid,)
    )
    with a.db.get_cursor() as cur:
        perm.bump(cur)
    a.audit("provider_deleted", pid)
    return {"ok": True}


class DefaultProvider(BaseModel):
    provider_id: str | None = None
    model_id: str | None = Field(None, max_length=200)


@router.put("/account/provider")
def choose_provider(req: DefaultProvider):
    return api_models.save_preferences(req.provider_id, req.model_id, api_models.preferences()["retry_count"])


class RetryPreference(BaseModel):
    retry_count: int = Field(ge=0, le=10, strict=True)


@router.get("/account/api-preferences")
def api_preferences():
    return api_models.selection_status(api_models.preferences())


@router.put("/account/api-preferences")
def retry_preference(req: RetryPreference):
    pref = api_models.preferences()
    # Retry preference can be changed even when a previous selection is invalid.
    a.execute("""INSERT INTO api_preferences VALUES (?,?,?) ON CONFLICT(user_id)
      DO UPDATE SET retry_count=excluded.retry_count""", (a.user()["id"], pref["model_id"], req.retry_count))
    return api_preferences()


@router.get("/sessions/{session_id}/model-selection")
def get_session_model(session_id: str):
    return api_models.session_selection(session_id)


@router.put("/sessions/{session_id}/model-selection")
def set_session_model(session_id: str, req: DefaultProvider):
    if not req.provider_id or not req.model_id:
        raise HTTPException(422, "请选择 API 与模型")
    return api_models.save_session_selection(session_id, req.provider_id, req.model_id)


@router.get("/providers/{pid}/members")
def provider_members(pid: str):
    perm.manage_resource("api", pid)
    return [
        u
        for u in perm.visible_members()
        if "use" in perm.resource("api", pid, u["id"])["actions"]
    ]


@router.put("/providers/{pid}/members/{uid}")
def grant_provider(pid: str, uid: str):
    perm.write_grant(
        "api",
        pid,
        dict(subject_type="user", subject_id=uid, effect="allow", actions=["use"]),
    )
    return {"ok": True}


@router.delete("/providers/{pid}/members/{uid}")
def revoke_provider(pid: str, uid: str):
    perm.write_grant(
        "api",
        pid,
        dict(subject_type="user", subject_id=uid, effect="remove", actions=[]),
    )
    return {"ok": True}


@router.post("/providers/{pid}/test")
def test_provider(pid: str, model_id: str | None = None):
    p = a.resolve_provider(pid, model_id)
    a.bind_provider(pid, model_id=model_id)
    a.rate_limit("provider-test:" + a.user()["id"], 20, 60)
    from src.core.account_llm import chat_client

    client = chat_client()
    from src.core.api_retry import RetryBudget, RetryDeferredError, call_sync
    try:
        provider = next(iter(client._providers.values()))
        answer = call_sync(lambda timeout: provider.test_connection(), RetryBudget.for_scene("test"),
                           "test", check=client.check_access)
        client.check_access()
    except HTTPException:
        raise
    except RetryDeferredError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception:
        api_models.record_test(p, "connection", "failed", "连接失败，请检查地址、密钥和模型")
        raise HTTPException(502, "连接失败，请检查 API 地址、密钥和模型名称")
    finally:
        client.close()
    api_models.record_test(p, "connection", "passed" if answer else "failed", "连接成功" if answer else "模型返回空内容")
    if not answer:
        raise HTTPException(502, "模型返回空内容")
    return {"ok": True, "name": p["name"]}


@router.post("/providers/{pid}/test-tools")
async def test_provider_tools(pid: str, model_id: str | None = None):
    import asyncio
    from src.core.tool_chat import probe_tools, ToolProtocolError
    from src.core.api_retry import RetryDeferredError

    p = a.resolve_provider(pid, model_id)
    a.bind_provider(pid, model_id=model_id)
    a.rate_limit("provider-test:" + a.user()["id"], 20, 60)
    try:
        result = await asyncio.wait_for(probe_tools(), 60)
        api_models.record_test(p, "tools", "passed" if result["supported"] else "failed" if result["supported"] is False else "unknown", result["message"])
        return result
    except HTTPException:
        raise
    except (ToolProtocolError, RetryDeferredError) as exc:
        api_models.record_test(p, "tools", "unknown", "检测未完成，请重新检查连接与模型协议")
        return {"supported": None, "message": str(exc)}
    except Exception:
        api_models.record_test(p, "tools", "unknown", "检测未完成，请重新检查连接与模型协议")
        return {"supported": None, "message": "检测未完成，暂不能判断支持情况。请检查连接、模型和服务商工具协议。"}


@router.get("/admin/audit")
def audit_log():
    perm.require("audit.view")
    from src.core.audit_display import present_audit
    return present_audit(a.rows(
        "SELECT a.*,u.username FROM auth_audit a LEFT JOIN auth_users u ON a.actor=u.id ORDER BY a.id DESC LIMIT 200"
    ))


@router.delete("/admin/audit", status_code=204)
def clear_audit_log():
    perm.require("audit.clear")
    # Keep a record of who cleared the audit, in the same transaction.
    import time
    with a.db.get_cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("DELETE FROM auth_audit")
        cur.execute("INSERT INTO auth_audit(actor,action,target,created_at) VALUES (?,?,?,?)",
                    (a.user()["id"], "audit_cleared", "", int(time.time() * 1000)))
        cur.connection.commit()
    return Response(status_code=204)


class PersonalPreferencesRequest(BaseModel):
    content: str = Field('', max_length=4000)
    enabled: bool = True
    version: str = Field('', max_length=64)


@router.get('/account/preferences')
def get_personal_preferences():
    from src.core import personal_preferences
    a.user()
    return personal_preferences.read()


@router.put('/account/preferences')
def save_personal_preferences(req: PersonalPreferencesRequest):
    from src.core import personal_preferences
    a.user()
    return personal_preferences.save(req.content, req.enabled, req.version)
