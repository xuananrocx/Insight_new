"""Explicit model edits and their live selection impact; never rewrite job snapshots."""

import hashlib
import json

from fastapi import HTTPException


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def edit_version(cur, provider):
    catalog = [
        dict(r)
        for r in cur.execute(
            """SELECT m.model_id,m.display_name,m.revision,l.context_tokens,l.output_tokens,w.mode AS web_search
        FROM api_models m LEFT JOIN api_model_limits l USING(provider_id,model_id) LEFT JOIN api_model_web w USING(provider_id,model_id)
        WHERE m.provider_id=? ORDER BY m.model_id""",
            (provider["id"],),
        )
    ]
    # The encrypted secret participates in the digest but never leaves the server.
    return fingerprint([dict(provider), catalog])


def plan(cur, provider, req, catalog):
    current = {
        r["model_id"]: dict(r)
        for r in cur.execute(
            "SELECT * FROM api_models WHERE provider_id=?", (provider["id"],)
        )
    }
    renames, origins = {}, set()
    ids = {m.model_id for m in catalog}
    for model in catalog:
        source = model.original_model_id
        if source is None:
            continue
        if source in origins or source not in current:
            raise HTTPException(409, "原模型已变化，请重新打开编辑窗口")
        origins.add(source)
        if source != model.model_id:
            if model.model_id in current or source in ids:
                raise HTTPException(
                    409, "目标模型 ID 已存在，不能合并、交换或重复保留原模型"
                )
            renames[source] = model.model_id
    deleted = sorted(set(current) - ids - set(renames))
    defaults, sessions = {}, {}
    for source in [*renames, *deleted]:
        defaults[source] = [
            r[0]
            for r in cur.execute(
                """SELECT u.id FROM auth_users u LEFT JOIN api_preferences p ON p.user_id=u.id
            WHERE u.default_provider=? AND COALESCE(NULLIF(p.model_id,''),?)=? ORDER BY u.id""",
                (provider["id"], provider["chat_model"], source),
            )
        ]
        sessions[source] = [
            r[0]
            for r in cur.execute(
                """SELECT session_id FROM api_session_selection WHERE provider_id=?
            AND COALESCE(NULLIF(model_id,''),?)=? ORDER BY session_id""",
                (provider["id"], provider["chat_model"], source),
            )
        ]
    public = dict(
        renames=[
            dict(
                old_model_id=old,
                model_id=new,
                defaults=len(defaults[old]),
                sessions=len(sessions[old]),
            )
            for old, new in renames.items()
        ],
        deletions=[
            dict(model_id=old, defaults=len(defaults[old]), sessions=len(sessions[old]))
            for old in deleted
        ],
    )
    token = fingerprint(
        [
            edit_version(cur, provider),
            req.model_dump(exclude={"confirmation_token"}),
            defaults,
            sessions,
        ]
    )
    return renames, public, token


def migrate_selections(cur, provider_id, renames):
    for old, new in renames.items():
        cur.execute(
            """UPDATE api_preferences SET model_id=? WHERE model_id=? AND user_id IN
                    (SELECT id FROM auth_users WHERE default_provider=?)""",
            (new, old, provider_id),
        )
        cur.execute(
            "UPDATE api_session_selection SET model_id=? WHERE provider_id=? AND model_id=?",
            (new, provider_id, old),
        )
