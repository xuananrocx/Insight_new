"""Single-team accounts, resource grants and encrypted chat credentials.

All identities come from server sessions, never from a caller supplied user ID.
The independent schema leaves existing knowledge/vector IDs intact.
"""

from __future__ import annotations

import contextvars
import hashlib
import os
import secrets
import sqlite3
import time
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from cryptography.fernet import Fernet
from fastapi import HTTPException

from src.core.config import USER_DATA_DIR, settings
from src.db import metadata_db as db

identity: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "identity", default=None
)
selected_provider: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "selected_provider", default=None
)
active_kb: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "active_kb", default=None
)
provider_snapshot: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "provider_snapshot", default=None
)
enabled = False
passwords = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_dummy_hash = passwords.hash(secrets.token_urlsafe(32))
COOKIE = "insight_session"
SESSION_SECONDS = 12 * 3600
ROLES = {"reader": 1, "editor": 2, "manager": 3, "owner": 4}


def rows(sql: str, args=()) -> list[dict]:
    with db.get_cursor() as cur:
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]


def execute(sql: str, args=()) -> None:
    with db.get_cursor() as cur:
        cur.execute(sql, args)


def secret_file(name: str, factory) -> bytes:
    path = USER_DATA_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes().strip()
    with os.fdopen(fd, "wb") as f:
        value = factory()
        f.write(value)
    return value


def cipher() -> Fernet:
    return Fernet(secret_file("credentials.key", Fernet.generate_key))


def init_auth() -> None:
    global enabled
    with db.get_cursor() as cur:
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS auth_users (
          id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'member',
          disabled INTEGER NOT NULL DEFAULT 0, must_change_password INTEGER NOT NULL DEFAULT 1,
          default_provider TEXT, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS auth_sessions (
          token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES auth_users(id),
          csrf TEXT NOT NULL, expires_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS auth_sessions_user ON auth_sessions(user_id);
        CREATE TABLE IF NOT EXISTS auth_objects (
          kind TEXT NOT NULL, object_id TEXT NOT NULL, owner_id TEXT NOT NULL REFERENCES auth_users(id),
          PRIMARY KEY(kind, object_id));
        CREATE INDEX IF NOT EXISTS auth_objects_owner ON auth_objects(owner_id,kind);
        CREATE TABLE IF NOT EXISTS auth_kb_grants (
          kb_id TEXT NOT NULL REFERENCES kbs(id) ON DELETE CASCADE,
          user_id TEXT NOT NULL REFERENCES auth_users(id), role TEXT NOT NULL,
          PRIMARY KEY(kb_id,user_id));
        CREATE TABLE IF NOT EXISTS auth_providers (
          id TEXT PRIMARY KEY, owner_id TEXT REFERENCES auth_users(id), scope TEXT NOT NULL,
          name TEXT NOT NULL, protocol TEXT NOT NULL, base_url TEXT NOT NULL,
          chat_model TEXT NOT NULL, secret TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS auth_provider_grants (
          provider_id TEXT NOT NULL REFERENCES auth_providers(id) ON DELETE CASCADE,
          user_id TEXT NOT NULL REFERENCES auth_users(id), PRIMARY KEY(provider_id,user_id));
        CREATE TABLE IF NOT EXISTS auth_limits (bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, until INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS auth_job_context (
          task_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, provider_id TEXT,
          started_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS auth_audit (
          id INTEGER PRIMARY KEY, actor TEXT, action TEXT NOT NULL, target TEXT, created_at INTEGER NOT NULL);
        """)
    if not rows("SELECT id FROM auth_users LIMIT 1"):
        secret_file("setup-token.txt", lambda: secrets.token_urlsafe(32).encode())
    cipher()
    from src.core import permissions

    permissions.init()
    enabled = True


def user() -> dict:
    current = identity.get()
    if not current:
        raise HTTPException(401, "请先登录")
    if current.get("csrf") and not rows(
        "SELECT 1 FROM auth_sessions WHERE user_id=? AND csrf=? AND expires_at>?",
        (current["id"], current["csrf"], int(time.time())),
    ):
        raise HTTPException(401, "登录已失效，请重新登录")
    found = rows("SELECT * FROM auth_users WHERE id=? AND disabled=0", (current["id"],))
    if not found:
        raise HTTPException(401, "账号已停用，请重新登录")
    return found[0]


def admin() -> dict:
    from src.core import permissions

    current = user()
    if not permissions.is_super(current["id"]):
        raise HTTPException(403, "需要超级管理员权限")
    return current


def public_user(u: dict) -> dict:
    from src.core import permissions as p

    result = {
        k: u[k]
        for k in (
            "id",
            "username",
            "role",
            "disabled",
            "must_change_password",
            "default_provider",
        )
    }
    result.update(
        roles=[
            {"id": r["id"], "name": r["name"], "enabled": bool(r["enabled"])}
            for r in p.roles(u["id"])
        ],
        permissions=sorted(p.effective(u["id"])),
        is_super=p.is_super(u["id"]),
        permission_revision=p.revision(),
    )
    profile = rows(
        "SELECT display_name,password_reset_at FROM permission_profiles WHERE user_id=?",
        (u["id"],),
    )
    if profile:
        result.update(profile[0])
    return result


def validate_password(value: str) -> None:
    if not 12 <= len(value) <= 128:
        raise HTTPException(400, "密码长度需要为 12～128 个字符")


def check_password(encoded: str, password: str) -> bool:
    try:
        return passwords.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def rate_limit(bucket: str, maximum=12, seconds=900) -> None:
    now = int(time.time())
    with db.get_cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("DELETE FROM auth_limits WHERE until<?", (now,))
        cur.execute(
            "INSERT INTO auth_limits VALUES (?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1",
            (bucket, now + seconds),
        )
        cur.execute("SELECT count FROM auth_limits WHERE bucket=?", (bucket,))
        count = cur.fetchone()[0]
        cur.connection.commit()
    if count > maximum:
        raise HTTPException(429, "尝试次数过多，请稍后再试")


def audit(action: str, target: str = "") -> None:
    current = identity.get()
    execute(
        "INSERT INTO auth_audit(actor,action,target,created_at) VALUES (?,?,?,?)",
        (current["id"] if current else None, action, target, int(time.time() * 1000)),
    )


def claim(kind: str, object_id: str, cur=None, owner_id: str | None = None) -> None:
    current = identity.get()
    owner = owner_id or (current["id"] if current else None)
    if not enabled or not owner:
        return
    sql = "INSERT INTO auth_objects(kind,object_id,owner_id) VALUES (?,?,?) ON CONFLICT(kind,object_id) DO UPDATE SET owner_id=excluded.owner_id"
    if cur is not None:
        cur.execute(sql, (kind, str(object_id), owner))
    else:
        execute(sql, (kind, str(object_id), owner))


def owns(kind: str, object_id: str) -> bool:
    return bool(
        rows(
            "SELECT 1 FROM auth_objects WHERE kind=? AND object_id=? AND owner_id=?",
            (kind, str(object_id), user()["id"]),
        )
    )


def require_owner(kind: str, object_id: str) -> None:
    if not owns(kind, object_id):
        raise HTTPException(404, "记录不存在或无权访问")


def kb_role(kb_id: str) -> str | None:
    from src.core import permissions as p

    access = p.resource("kb", kb_id)
    if not access["actions"]:
        return None
    if access["owner"]:
        return "owner"
    for role, action in [
        ("manager", "manage"),
        ("editor", "edit"),
        ("reader", "query"),
    ]:
        if action in access["actions"]:
            return role
    return None


def require_kb(kb_id: str | None, role="reader") -> None:
    from src.core import permissions as p

    if not kb_id:
        raise HTTPException(403, "没有此知识库的使用权限，历史会话仍可查看")
    if role == "owner" and not owns("kb", kb_id):
        raise HTTPException(403, "需要知识库所有者权限")
    p.require_resource(
        "kb",
        kb_id,
        {
            "reader": "query",
            "editor": "edit",
            "manager": "manage",
            "owner": "query",
        }.get(role, role),
    )


def visible_kb_ids() -> list[str]:
    from src.core import permissions

    return permissions.visible_kbs()


def resolve_provider(provider_id: str | None = None) -> dict:
    from src.core import permissions

    current = user()
    pid = provider_id if provider_id is not None else current["default_provider"]
    if not pid:
        raise HTTPException(409, "请先选择个人 API 或已获授权的团队 API")
    found = rows("SELECT * FROM auth_providers WHERE id=? AND enabled=1", (pid,))
    if not found:
        raise HTTPException(403, "所选 API 已停用或删除，请重新选择")
    provider = found[0]
    if provider["scope"] == "personal":
        permissions.require("api.personal")
        if provider["owner_id"] != current["id"]:
            raise HTTPException(403, "没有此 API 的使用权限")
    else:
        permissions.require_resource("api", pid, "use")
    return provider


def provider_config(p: dict) -> dict:
    return {
        "protocol": p["protocol"],
        "base_url": p["base_url"],
        "chat_model": p["chat_model"],
        "request_timeout_seconds": settings.config.get("llm", {}).get(
            "request_timeout_seconds", 120
        ),
        "api_key": cipher().decrypt(p["secret"].encode()).decode(),
    }


def bind_provider(provider_id: str | None = None, required: bool = True) -> None:
    """Freeze selection and configuration once; permission is still rechecked at use."""
    try:
        p = resolve_provider(provider_id)
    except HTTPException:
        if required:
            raise
        selected_provider.set("")
        provider_snapshot.set(None)
        return
    selected_provider.set(p["id"])
    provider_snapshot.set(p)


def bootstrap(username: str, password: str, token: str) -> None:
    validate_password(password)
    path = USER_DATA_DIR / "setup-token.txt"
    if not path.exists() or not secrets.compare_digest(path.read_text().strip(), token):
        raise HTTPException(403, "初始化令牌不正确，请从服务器 setup-token.txt 读取")
    uid = uuid.uuid4().hex
    hashed = passwords.hash(password)
    # SQLite backup includes WAL pages, unlike copying the DB file during writes.
    backup_path = USER_DATA_DIR / "before-accounts.sqlite3"
    if not backup_path.exists():
        with (
            sqlite3.connect(db._get_db_path()) as source,
            sqlite3.connect(backup_path) as backup,
        ):
            source.backup(backup)
        backup_path.chmod(0o600)
    # One transaction: no partial owner migration, and two simultaneous setup requests cannot win.
    with db.get_cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("SELECT 1 FROM auth_users LIMIT 1")
        if cur.fetchone():
            raise HTTPException(409, "系统已经初始化")
        cur.execute(
            "INSERT INTO auth_users(id,username,password_hash,role,must_change_password,created_at) VALUES (?,?,?,'admin',0,?)",
            (uid, username, hashed, int(time.time())),
        )
        for table, kind in [
            ("kbs", "kb"),
            ("sessions", "session"),
            ("ai_call_logs", "log"),
            ("upload_tasks", "task"),
            ("feedback_queue", "feedback"),
        ]:
            cur.execute(
                f"INSERT INTO auth_objects SELECT ?,CAST(id AS TEXT),? FROM {table}",
                (kind, uid),
            )
        for name, cfg in settings.config.get("llm", {}).get("providers", {}).items():
            if not cfg.get("chat_model"):
                continue
            key = settings.resolve_api_key(cfg) or ""
            pid = uuid.uuid4().hex
            cur.execute(
                "INSERT INTO auth_providers VALUES (?,?,'personal',?,?,?,?,?,1)",
                (
                    pid,
                    uid,
                    name,
                    cfg.get("protocol", "openai"),
                    cfg.get("base_url", ""),
                    cfg["chat_model"],
                    cipher().encrypt(key.encode()).decode(),
                ),
            )
            if name == settings.chat_provider:
                cur.execute(
                    "UPDATE auth_users SET default_provider=? WHERE id=?", (pid, uid)
                )
        cur.connection.commit()
    from src.core import permissions

    permissions.init()
    path.unlink(missing_ok=True)


def session_identity(token: str | None) -> dict | None:
    if not token:
        return None
    found = rows(
        """SELECT u.*,s.csrf FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id
                    WHERE s.token_hash=? AND s.expires_at>? AND u.disabled=0""",
        (hashlib.sha256(token.encode()).hexdigest(), int(time.time())),
    )
    return found[0] if found else None


def login(username: str, password: str) -> tuple[str, dict]:
    found = rows("SELECT * FROM auth_users WHERE username=?", (username,))
    u = found[0] if found else None
    valid = check_password(u["password_hash"] if u else _dummy_hash, password)
    if not valid or not u or u["disabled"]:
        raise HTTPException(401, "用户名或密码不正确，或账号已停用")
    token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    execute("DELETE FROM auth_sessions WHERE expires_at<?", (int(time.time()),))
    execute(
        "INSERT INTO auth_sessions VALUES (?,?,?,?)",
        (
            hashlib.sha256(token.encode()).hexdigest(),
            u["id"],
            csrf,
            int(time.time()) + SESSION_SECONDS,
        ),
    )
    execute(
        "UPDATE permission_profiles SET last_login=? WHERE user_id=?",
        (int(time.time()), u["id"]),
    )
    return token, {**public_user(u), "csrf": csrf}
