"""Account and provider management; no public registration endpoint."""

from __future__ import annotations

import hashlib
import uuid
from urllib.parse import urlsplit
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from src.core import accounts as a, permissions as perm

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
    return a.rows(
        "SELECT id,username FROM auth_users WHERE disabled=0 ORDER BY username"
    )


@router.get("/kbs/{kb_id}/members")
def kb_members(kb_id: str):
    perm.manage_resource("kb", kb_id)
    users = a.rows("SELECT id,username FROM auth_users ORDER BY username")
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


class ProviderInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scope: Literal["personal", "team"] = "personal"
    protocol: Literal["openai", "anthropic", "anthropic-arch"] = "openai"
    base_url: str = Field(min_length=1, max_length=2048)
    chat_model: str = Field(min_length=1, max_length=200)
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
        provider["has_api_key"] = bool(
            a.cipher().decrypt(provider.pop("secret").encode())
        )
        provider.update(
            usable=usable,
            manageable=perm.has("api.personal" if personal else "api.manage"),
            grantable=not personal and perm.has("api.grant"),
        )
        items.append(provider)
    return {"items": items, "default_provider": u["default_provider"]}


def save_provider(req: ProviderInput, pid: str | None = None):
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
    pid = pid or uuid.uuid4().hex
    if old:
        a.execute(
            "UPDATE auth_providers SET name=?,protocol=?,base_url=?,chat_model=?,secret=?,enabled=? WHERE id=?",
            (
                req.name,
                req.protocol,
                req.base_url.strip().rstrip("/"),
                req.chat_model,
                secret,
                int(req.enabled),
                pid,
            ),
        )
    else:
        a.execute(
            "INSERT INTO auth_providers VALUES (?,?,?,?,?,?,?,?,?)",
            (
                pid,
                u["id"] if req.scope == "personal" else None,
                req.scope,
                req.name,
                req.protocol,
                req.base_url.strip().rstrip("/"),
                req.chat_model,
                secret,
                int(req.enabled),
            ),
        )
    with a.db.get_cursor() as cur:
        perm.bump(cur)
    a.audit("provider_saved", pid)
    return {"id": pid}


@router.post("/providers", status_code=201)
def create_provider(req: ProviderInput):
    return save_provider(req)


@router.put("/providers/{pid}")
def update_provider(pid: str, req: ProviderInput):
    return save_provider(req, pid)


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


@router.put("/account/provider")
def choose_provider(req: DefaultProvider):
    if req.provider_id:
        a.resolve_provider(req.provider_id)
    a.execute(
        "UPDATE auth_users SET default_provider=? WHERE id=?",
        (req.provider_id, a.user()["id"]),
    )
    return {"ok": True}


@router.get("/providers/{pid}/members")
def provider_members(pid: str):
    perm.manage_resource("api", pid)
    return [
        u
        for u in a.rows("SELECT id,username FROM auth_users")
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
def test_provider(pid: str):
    p = a.resolve_provider(pid)
    a.bind_provider(pid)
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
        raise HTTPException(502, "连接失败，请检查 API 地址、密钥和模型名称")
    finally:
        client.close()
    return {"ok": bool(answer), "name": p["name"]}


@router.post("/providers/{pid}/test-tools")
async def test_provider_tools(pid: str):
    import asyncio
    from src.core.tool_chat import probe_tools, ToolProtocolError
    from src.core.api_retry import RetryDeferredError

    a.resolve_provider(pid)
    a.bind_provider(pid)
    a.rate_limit("provider-test:" + a.user()["id"], 20, 60)
    try:
        return await asyncio.wait_for(probe_tools(), 60)
    except HTTPException:
        raise
    except (ToolProtocolError, RetryDeferredError) as exc:
        return {"supported": None, "message": str(exc)}
    except Exception:
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
