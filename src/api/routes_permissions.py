"""Role, user and resource administration with bounded delegation and previews."""

import json
import sqlite3
import time
import uuid
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from src.core import accounts as a, permissions as p

router = APIRouter(prefix="/api/v1", tags=["permissions"])


@router.get("/permissions/catalog")
def catalog():
    a.user()
    return {
        "items": [
            {
                "key": key,
                "group": value[0],
                "label": value[1],
                "requires": p.DEPENDENCIES.get(key),
            }
            for key, value in p.CATALOG.items()
        ],
        "revision": p.revision(),
    }


def role_payload(r):
    return {
        **r,
        "permissions": json.loads(r["permissions"]),
        "users_count": a.rows(
            "SELECT COUNT(*) n FROM permission_user_roles WHERE role_id=?", (r["id"],)
        )[0]["n"],
    }


@router.get("/roles/directory")
def role_directory():
    a.user()
    return a.rows("SELECT id,name,enabled FROM permission_roles ORDER BY name")


@router.get("/admin/roles")
def list_roles():
    if not (p.has("roles.view") or p.has("users.roles") or p.has("users.create")):
        p.require("roles.view")
    return [
        role_payload(r)
        for r in a.rows("SELECT * FROM permission_roles ORDER BY protected DESC,name")
    ]


@router.get("/admin/roles/{rid}/members")
def role_members(rid: str):
    p.require("roles.view")
    return a.rows(
        "SELECT u.id,u.username,u.disabled FROM auth_users u JOIN permission_user_roles ur ON ur.user_id=u.id WHERE ur.role_id=? ORDER BY u.username",
        (rid,),
    )


class RoleInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field("", max_length=500)
    permissions: list[str] = Field(default_factory=list, max_length=100)
    enabled: bool = True
    expected_revision: int | None = None


def role_change(rid, req):
    p.require("roles.manage")
    old = a.rows("SELECT * FROM permission_roles WHERE id=?", (rid,)) if rid else []
    if not req.name.strip():
        raise HTTPException(400, "角色名称不能为空")
    if rid and not old:
        raise HTTPException(404, "角色不存在")
    if old and old[0]["protected"]:
        raise HTTPException(400, "超级管理员角色受保护，不能编辑、停用或删除")
    p.validate_caps(req.permissions)
    p.ceiling(req.permissions)
    if old:
        p.ceiling(json.loads(old[0]["permissions"]))
    return old[0] if old else None


@router.post("/admin/roles/{rid}/preview")
def preview_role(rid: str, req: RoleInput):
    preview_revision = p.revision()
    old = role_change(rid, req)
    affected = a.rows(
        "SELECT u.id,u.username FROM auth_users u JOIN permission_user_roles ur ON u.id=ur.user_id WHERE ur.role_id=?",
        (rid,),
    )
    changes = []
    override = {
        **old,
        "permissions": json.dumps(req.permissions),
        "enabled": req.enabled,
    }
    resources = a.rows(
        "SELECT DISTINCT kind,resource_id FROM permission_grants WHERE subject_type='role' AND subject_id=?",
        (rid,),
    )
    for u in affected:
        before = p.effective(u["id"])
        others = {
            cap
            for r in p.roles(u["id"])
            if r["id"] != rid and r["enabled"]
            for cap in json.loads(r["permissions"])
        }
        after = others | (set(req.permissions) if req.enabled else set())
        resource_changes = []
        for r in resources:
            try:
                left = p.resource(r["kind"], r["resource_id"], u["id"])["actions"]
                right = p.resource(
                    r["kind"], r["resource_id"], u["id"], role_override=override
                )["actions"]
            except HTTPException:
                continue
            if left != right:
                resource_changes.append({**r, "before": left, "after": right})
        changes.append(
            {
                **u,
                "added": sorted(after - before),
                "removed": sorted(before - after),
                "resources": resource_changes,
            }
        )
    return {
        "revision": preview_revision,
        "affected_count": len(changes),
        "changes": changes,
    }


@router.post("/admin/roles", status_code=201)
def create_role(req: RoleInput):
    rid = uuid.uuid4().hex
    try:
        with a.db.get_cursor() as cur:
            cur.execute("BEGIN IMMEDIATE")
            p.check_revision(cur, req.expected_revision)
            role_change(None, req)
            cur.execute(
                "INSERT INTO permission_roles(id,name,description,permissions,enabled) VALUES (?,?,?,?,?)",
                (
                    rid,
                    req.name.strip(),
                    req.description,
                    json.dumps(sorted(set(req.permissions))),
                    int(req.enabled),
                ),
            )
            p.bump(cur)
            cur.connection.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, "角色名称已存在")
    a.audit("role_created", rid)
    return {"id": rid}


@router.put("/admin/roles/{rid}")
def update_role(rid: str, req: RoleInput):
    try:
        with a.db.get_cursor() as cur:
            cur.execute("BEGIN IMMEDIATE")
            p.check_revision(cur, req.expected_revision)
            old = role_change(rid, req)
            cur.execute(
                "UPDATE permission_roles SET name=?,description=?,permissions=?,enabled=? WHERE id=?",
                (
                    req.name.strip(),
                    req.description,
                    json.dumps(sorted(set(req.permissions))),
                    int(req.enabled),
                    rid,
                ),
            )
            p.bump(cur)
            cur.connection.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, "角色名称已存在")
    a.audit(
        "role_updated",
        json.dumps(
            {
                "id": rid,
                "before": json.loads(old["permissions"]),
                "after": req.permissions,
                "enabled": req.enabled,
            },
            ensure_ascii=False,
        ),
    )
    return {"ok": True}


@router.delete("/admin/roles/{rid}")
def delete_role(rid: str):
    p.require("roles.manage")
    with a.db.get_cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        found = a.rows("SELECT * FROM permission_roles WHERE id=?", (rid,))
        if not found:
            raise HTTPException(404, "角色不存在")
        if found[0]["protected"]:
            raise HTTPException(400, "不能删除受保护角色")
        p.ceiling(json.loads(found[0]["permissions"]))
        if a.rows(
            "SELECT 1 FROM permission_user_roles WHERE role_id=?", (rid,)
        ) or a.rows(
            "SELECT 1 FROM permission_grants WHERE subject_type='role' AND subject_id=?",
            (rid,),
        ):
            raise HTTPException(409, "角色仍关联用户或资源授权，请先替换角色并移除授权")
        cur.execute("DELETE FROM permission_roles WHERE id=?", (rid,))
        p.bump(cur)
        cur.connection.commit()
    a.audit("role_deleted", rid)
    return {"ok": True}


def user_payload(u):
    profile = a.rows(
        "SELECT display_name,note,last_login,password_reset_at FROM permission_profiles WHERE user_id=?",
        (u["id"],),
    )
    return {
        **a.public_user(u),
        **(profile[0] if profile else {}),
        "created_at": u["created_at"],
    }


@router.get("/admin/users")
def users():
    p.require("users.view")
    return [
        user_payload(u)
        for u in a.rows("SELECT * FROM auth_users ORDER BY created_at,id")
    ]


class NewUser(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_.@-]+$")
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field("", max_length=80)
    note: str = Field("", max_length=500)
    role_ids: list[str] | None = None
    role: Literal["admin", "member"] = "member"


@router.post("/admin/users", status_code=201)
def create_user(req: NewUser):
    p.require("users.create")
    a.validate_password(req.password)
    ids = req.role_ids if req.role_ids is not None else [req.role]
    # Account creation itself only grants the ordinary template without role assignment permission.
    if ids != ["member"]:
        p.require("users.roles")
    uid = uuid.uuid4().hex
    password_hash = a.passwords.hash(req.password)
    try:
        with a.db.get_cursor() as cur:
            cur.execute("BEGIN IMMEDIATE")
            p.require("users.create")
            p.selected_roles(ids)
            cur.execute(
                "INSERT INTO auth_users(id,username,password_hash,role,created_at) VALUES (?,?,?,'member',?)",
                (uid, req.username, password_hash, int(time.time())),
            )
            cur.execute(
                "INSERT INTO permission_profiles(user_id,display_name,note) VALUES (?,?,?)",
                (uid, req.display_name, req.note),
            )
            cur.executemany(
                "INSERT INTO permission_user_roles VALUES (?,?)",
                [(uid, rid) for rid in set(ids)],
            )
            p.bump(cur)
            cur.connection.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, "用户名已存在")
    a.audit("user_created", uid)
    return {"id": uid}


class UserChange(BaseModel):
    display_name: str | None = Field(None, max_length=80)
    note: str | None = Field(None, max_length=500)
    role_ids: list[str] | None = None
    role: Literal["admin", "member"] | None = None
    disabled: bool | None = None
    password: str | None = Field(None, min_length=12, max_length=128)
    expected_revision: int | None = None


def user_change(uid, req):
    actor = a.user()
    target = p.target_user(uid)
    ids = (
        req.role_ids if req.role_ids is not None else ([req.role] if req.role else None)
    )
    if ids is not None:
        p.require("users.roles")
        p.selected_roles(ids)
    if req.display_name is not None or req.note is not None:
        p.require("users.edit")
    if req.disabled is not None:
        p.require("users.disable")
    if req.password is not None:
        p.require("users.password")
    if uid == actor["id"] and (
        ids is not None or req.disabled is not None or req.password is not None
    ):
        raise HTTPException(
            400, "不能在此修改自己的角色、状态或密码，请由另一名有权限的管理员操作"
        )
    return target, ids


@router.post("/admin/users/{uid}/preview")
def preview_user(uid: str, req: UserChange):
    preview_revision = p.revision()
    target, ids = user_change(uid, req)
    before = p.effective(uid)
    after = (
        {cap for r in p.selected_roles(ids) for cap in json.loads(r["permissions"])}
        if ids is not None
        else before
    )
    resources = []
    if req.disabled is True:
        after = set()
    if ids is not None or req.disabled is not None:
        for r in resource_directory():
            left = p.resource(r["kind"], r["id"], uid)["actions"]
            right = p.resource(
                r["kind"],
                r["id"],
                uid,
                role_ids_override=ids,
                disabled_override=req.disabled,
            )["actions"]
            if left != right:
                resources.append(
                    {
                        "kind": r["kind"],
                        "resource_id": r["id"],
                        "before": left,
                        "after": right,
                    }
                )
    item = {
        "username": target["username"],
        "added": sorted(after - before),
        "removed": sorted(before - after),
        "resources": resources,
    }
    if req.disabled is not None:
        item["disabled"] = req.disabled
    if req.password:
        item["reason"] = "重置密码将撤销全部登录会话，下次登录须修改密码"
    return {"revision": preview_revision, "affected_count": 1, "changes": [item]}


@router.patch("/admin/users/{uid}")
def update_user(uid: str, req: UserChange):
    hashed = a.passwords.hash(req.password) if req.password else None
    with a.db.get_cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        p.check_revision(cur, req.expected_revision)
        target, ids = user_change(uid, req)
        if p.is_super(uid):
            p.assert_super_survives(cur, uid, ids, req.disabled)
        if ids is not None:
            cur.execute("DELETE FROM permission_user_roles WHERE user_id=?", (uid,))
            cur.executemany(
                "INSERT INTO permission_user_roles VALUES (?,?)",
                [(uid, rid) for rid in set(ids)],
            )
        for field in ["display_name", "note"]:
            if getattr(req, field) is not None:
                cur.execute(
                    f"UPDATE permission_profiles SET {field}=? WHERE user_id=?",
                    (getattr(req, field), uid),
                )
        if req.disabled is not None:
            cur.execute(
                "UPDATE auth_users SET disabled=? WHERE id=?", (int(req.disabled), uid)
            )
        if hashed:
            cur.execute(
                "UPDATE auth_users SET password_hash=?,must_change_password=1 WHERE id=?",
                (hashed, uid),
            )
            cur.execute(
                "UPDATE permission_profiles SET password_reset_at=? WHERE user_id=?",
                (int(time.time()), uid),
            )
        if hashed or req.disabled:
            cur.execute("DELETE FROM auth_sessions WHERE user_id=?", (uid,))
        p.bump(cur)
        cur.connection.commit()
    safe = req.model_dump(exclude_none=True, exclude={"password", "expected_revision"})
    if hashed:
        safe["password_reset"] = True
    a.audit(
        "user_updated", json.dumps({"id": uid, "changes": safe}, ensure_ascii=False)
    )
    return {"ok": True}


@router.get("/admin/users/{uid}/sessions")
def user_sessions(uid: str):
    p.require("users.sessions")
    p.target_user(uid)
    # No cookie hashes or CSRF secrets are returned.
    return a.rows(
        "SELECT expires_at FROM auth_sessions WHERE user_id=? AND expires_at>? ORDER BY expires_at DESC",
        (uid, int(time.time())),
    )


@router.delete("/admin/users/{uid}/sessions")
def force_logout(uid: str):
    p.require("users.sessions")
    p.target_user(uid)
    if uid == a.user()["id"]:
        raise HTTPException(400, "请使用退出登录")
    a.execute("DELETE FROM auth_sessions WHERE user_id=?", (uid,))
    a.audit("user_forced_logout", uid)
    return {"ok": True}


@router.get("/admin/users/{uid}/resources")
def user_resources(uid: str):
    p.require("users.view")
    if not a.rows("SELECT 1 FROM auth_users WHERE id=?", (uid,)):
        raise HTTPException(404, "账号不存在")
    items = resource_directory()
    return [{**r, **p.resource(r["kind"], r["id"], uid)} for r in items]


@router.get("/access/resources")
def resource_directory():
    a.user()
    items = []
    for k in a.rows("SELECT id,name FROM kbs ORDER BY name"):
        if (
            a.owns("kb", k["id"])
            or "manage" in p.resource("kb", k["id"])["actions"]
            or (p.kb_policy(k["id"])["scope"] == "team" and p.has("kb.team_manage"))
        ):
            owner = a.rows(
                "SELECT u.id,u.username FROM auth_objects o JOIN auth_users u ON u.id=o.owner_id WHERE o.kind='kb' AND o.object_id=?",
                (k["id"],),
            )
            items.append(
                {
                    **k,
                    "kind": "kb",
                    **p.kb_policy(k["id"]),
                    "owner": owner[0] if owner else None,
                }
            )
    if p.has("api.view"):
        items.extend(
            {**r, "kind": "api"}
            for r in a.rows(
                "SELECT id,name,enabled,scope FROM auth_providers WHERE scope='team' ORDER BY name"
            )
        )
    return items


@router.get("/access/{kind}/{rid}")
def grants(kind: Literal["kb", "api"], rid: str):
    p.manage_resource(kind, rid)
    items = a.rows(
        "SELECT * FROM permission_grants WHERE kind=? AND resource_id=? ORDER BY subject_type,subject_id",
        (kind, rid),
    )
    for g in items:
        table, col = (
            ("auth_users", "username")
            if g["subject_type"] == "user"
            else ("permission_roles", "name")
        )
        found = a.rows(f"SELECT {col} name FROM {table} WHERE id=?", (g["subject_id"],))
        g["name"] = found[0]["name"] if found else "已删除的对象"
        g["actions"] = json.loads(g["actions"])
    return {"items": items, "revision": p.revision()}


class GrantChange(BaseModel):
    subject_type: Literal["user", "role"]
    subject_id: str
    effect: Literal["allow", "deny", "remove"] = "allow"
    actions: list[str] = Field(default_factory=list, max_length=10)
    expected_revision: int | None = None


@router.post("/access/{kind}/{rid}/preview")
def preview_grant(kind: Literal["kb", "api"], rid: str, req: GrantChange):
    preview_revision = p.revision()
    change = req.model_dump(exclude={"expected_revision"})
    p.grant_check(kind, rid, **change)
    users = (
        a.rows("SELECT id,username FROM auth_users WHERE id=?", (req.subject_id,))
        if req.subject_type == "user"
        else a.rows(
            "SELECT u.id,u.username FROM auth_users u JOIN permission_user_roles ur ON ur.user_id=u.id WHERE ur.role_id=?",
            (req.subject_id,),
        )
    )
    changes = []
    for u in users:
        before = p.resource(kind, rid, u["id"])
        after = p.resource(kind, rid, u["id"], override=change)
        changes.append(
            {
                **u,
                "before": before["actions"],
                "after": after["actions"],
                "added": sorted(set(after["actions"]) - set(before["actions"])),
                "removed": sorted(set(before["actions"]) - set(after["actions"])),
                "sources": after["sources"],
                "reason": after["reason"],
            }
        )
    return {
        "revision": preview_revision,
        "affected_count": len(changes),
        "changes": changes,
    }


@router.put("/access/{kind}/{rid}")
def set_grant(kind: Literal["kb", "api"], rid: str, req: GrantChange):
    p.write_grant(
        kind, rid, req.model_dump(exclude={"expected_revision"}), req.expected_revision
    )
    return {"ok": True}


@router.get("/access/{kind}/{rid}/effective/{uid}")
def explain(kind: Literal["kb", "api"], rid: str, uid: str):
    p.manage_resource(kind, rid)
    return p.resource(kind, rid, uid)


class PolicyChange(BaseModel):
    scope: Literal["private", "team"] | None = None
    enabled: bool | None = None
    owner_id: str | None = None
    expected_revision: int | None = None


@router.put("/access/kb/{rid}/policy")
def update_policy(rid: str, req: PolicyChange):
    with a.db.get_cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        p.check_revision(cur, req.expected_revision)
        p.manage_resource("kb", rid)
        old = p.kb_policy(rid)
        owns = a.owns("kb", rid)
        global_manager = old["scope"] == "team" and p.has("kb.team_manage")
        if not (owns or global_manager):
            raise HTTPException(403, "只有所有者或团队资源管理者可以修改归属及状态")
        if req.scope and req.scope != old["scope"]:
            if not owns:
                raise HTTPException(403, "转换归属必须由当前所有者操作")
            if req.scope == "team":
                p.require("kb.team_create")
            if req.scope == "private" and a.rows(
                "SELECT 1 FROM permission_grants WHERE kind='kb' AND resource_id=?",
                (rid,),
            ):
                raise HTTPException(409, "请先移除共享授权，再转为私有知识库")
        if req.owner_id or (req.scope and req.scope != old["scope"]):
            from src.core import kb_names
            kb = cur.execute("SELECT name FROM kbs WHERE id=?", (rid,)).fetchone()
            kb_names.check(cur, kb[0], kb_id=rid, scope=req.scope or old["scope"], owner_id=req.owner_id)
        if req.owner_id:
            if not a.rows(
                "SELECT 1 FROM auth_users WHERE id=? AND disabled=0", (req.owner_id,)
            ):
                raise HTTPException(400, "目标账号不存在或已停用")
            cur.execute(
                "UPDATE auth_objects SET owner_id=? WHERE kind='kb' AND object_id=?",
                (req.owner_id, rid),
            )
            cur.execute(
                "DELETE FROM permission_grants WHERE kind='kb' AND resource_id=? AND subject_type='user' AND subject_id=?",
                (rid, req.owner_id),
            )
        cur.execute(
            "INSERT INTO permission_kbs VALUES (?,?,?) ON CONFLICT(kb_id) DO UPDATE SET scope=excluded.scope,enabled=excluded.enabled",
            (
                rid,
                req.scope or old["scope"],
                int(req.enabled) if req.enabled is not None else old["enabled"],
            ),
        )
        p.bump(cur)
        cur.connection.commit()
    a.audit(
        "kb_policy_updated",
        json.dumps(
            {"id": rid, **req.model_dump(exclude_none=True)}, ensure_ascii=False
        ),
    )
    return {"ok": True}


@router.get("/admin/api-stats")
def api_stats():
    p.require("api.stats")
    return a.rows("""SELECT p.id,p.name,COUNT(l.id) calls,COALESCE(SUM(l.token_input),0) token_input,
      COALESCE(SUM(l.token_output),0) token_output,COALESCE(SUM(CASE WHEN l.success=0 THEN 1 ELSE 0 END),0) failures,
      AVG(l.duration_ms) avg_duration_ms FROM auth_providers p LEFT JOIN permission_call_usage c ON p.id=c.provider_id
      LEFT JOIN ai_call_logs l ON l.id=c.log_id WHERE p.scope='team' GROUP BY p.id,p.name""")
