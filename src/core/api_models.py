"""Account model catalog and explicit selections. Never choose a fallback provider."""

from __future__ import annotations

import time

from fastapi import HTTPException

from src.core import accounts as a


def init_schema():
    with a.db.get_cursor() as cur:
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS api_models (
          provider_id TEXT NOT NULL REFERENCES auth_providers(id) ON DELETE CASCADE,
          model_id TEXT NOT NULL, display_name TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0,
          connection_status TEXT, connection_message TEXT, connection_checked_at INTEGER,
          tools_status TEXT, tools_message TEXT, tools_checked_at INTEGER,
          PRIMARY KEY(provider_id, model_id));
        CREATE TABLE IF NOT EXISTS api_model_limits (
          provider_id TEXT NOT NULL, model_id TEXT NOT NULL, context_tokens INTEGER, output_tokens INTEGER,
          PRIMARY KEY(provider_id,model_id),
          FOREIGN KEY(provider_id,model_id) REFERENCES api_models(provider_id,model_id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS api_preferences (
          user_id TEXT PRIMARY KEY REFERENCES auth_users(id) ON DELETE CASCADE,
          model_id TEXT, retry_count INTEGER NOT NULL DEFAULT 10 CHECK(retry_count BETWEEN 0 AND 10));
        CREATE TABLE IF NOT EXISTS api_job_models (
          task_id TEXT PRIMARY KEY REFERENCES upload_tasks(id) ON DELETE CASCADE,
          model_id TEXT);
        CREATE TABLE IF NOT EXISTS api_session_selection (
          session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
          provider_id TEXT, model_id TEXT);
        """)
        # Every maintained provider has at least one model. Old bootstrap/clients
        # may create providers without a catalog; migrate these deterministically.
        cur.execute("""INSERT INTO api_models(provider_id,model_id)
          SELECT id,chat_model FROM auth_providers p WHERE NOT EXISTS
          (SELECT 1 FROM api_models m WHERE m.provider_id=p.id)""")
        cur.execute("""INSERT OR IGNORE INTO api_preferences(user_id,model_id)
          SELECT u.id,p.chat_model FROM auth_users u JOIN auth_providers p ON p.id=u.default_provider""")


def models(provider):
    found = a.rows(
        "SELECT m.*,l.context_tokens,l.output_tokens FROM api_models m LEFT JOIN api_model_limits l USING(provider_id,model_id) WHERE m.provider_id=? ORDER BY m.rowid", (provider["id"],)
    )
    return found or [dict(model_id=provider["chat_model"], display_name="")]


def preferences():
    user = a.user()
    saved = a.rows("SELECT * FROM api_preferences WHERE user_id=?", (user["id"],))
    pref = saved[0] if saved else {}
    pid = user["default_provider"]
    legacy = (
        a.rows("SELECT chat_model FROM auth_providers WHERE id=?", (pid,))
        if pid
        else []
    )
    return dict(
        provider_id=pid,
        model_id=pref.get("model_id") or (legacy[0]["chat_model"] if legacy else None),
        retry_count=pref.get("retry_count", 10),
    )


def selection_status(selection):
    result = dict(selection)
    try:
        if not result.get("provider_id"):
            raise HTTPException(409, "尚未选择 API 与模型")
        provider = a.resolve_provider(result["provider_id"], result.get("model_id"))
        result.update(
            valid=True,
            reason=None,
            provider_name=provider["name"],
            model_label=provider["model_label"],
            model_id=provider["chat_model"],
        )
    except HTTPException as exc:
        result.update(valid=False, reason=exc.detail)
    return result


def save_preferences(provider_id, model_id, retry_count):
    if provider_id:
        provider = a.resolve_provider(provider_id, model_id)
        model_id = provider["chat_model"]
    elif model_id:
        raise HTTPException(422, "选择模型时必须指定 API")
    uid = a.user()["id"]
    with a.db.get_cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            "UPDATE auth_users SET default_provider=? WHERE id=?", (provider_id, uid)
        )
        cur.execute(
            """INSERT INTO api_preferences VALUES (?,?,?) ON CONFLICT(user_id)
          DO UPDATE SET model_id=excluded.model_id,retry_count=excluded.retry_count""",
            (uid, model_id, retry_count),
        )
        cur.connection.commit()
    return selection_status(preferences())


def session_selection(session_id):
    a.require_owner("session", session_id)
    found = a.rows(
        "SELECT provider_id,model_id FROM api_session_selection WHERE session_id=?",
        (session_id,),
    )
    return selection_status(
        found[0]
        if found
        else {k: v for k, v in preferences().items() if k != "retry_count"}
    )


def save_session_selection(session_id, provider_id, model_id):
    a.require_owner("session", session_id)
    p = a.resolve_provider(provider_id, model_id)
    a.execute(
        """INSERT INTO api_session_selection VALUES (?,?,?) ON CONFLICT(session_id)
      DO UPDATE SET provider_id=excluded.provider_id,model_id=excluded.model_id""",
        (session_id, p["id"], p["chat_model"]),
    )
    return session_selection(session_id)


def freeze_session_default(session_id):
    pref = preferences()
    a.execute(
        "INSERT OR IGNORE INTO api_session_selection VALUES (?,?,?)",
        (session_id, pref["provider_id"], pref["model_id"]),
    )


def record_test(provider, kind, status, message):
    # A concurrent edit invalidates an in-flight result. Never attach a result
    # obtained with old credentials to a new configuration.
    latest = a.rows("SELECT * FROM auth_providers WHERE id=?", (provider["id"],))
    if not latest or any(
        latest[0][key] != provider[key] for key in ("secret", "base_url", "protocol")
    ):
        return
    if kind not in ("connection", "tools"):
        raise ValueError(kind)
    a.execute(
        f"UPDATE api_models SET {kind}_status=?,{kind}_message=?,{kind}_checked_at=? WHERE provider_id=? AND model_id=? AND revision=?",
        (
            status,
            message,
            int(time.time()),
            provider["id"],
            provider["chat_model"],
            provider.get("model_revision", 0),
        ),
    )
