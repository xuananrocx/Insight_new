"""Role capabilities and resource grants. Private data never has an admin bypass."""

from __future__ import annotations

import json
from fastapi import HTTPException
from src.core import accounts as a

CATALOG = {
    "documents.view": ("工作空间", "文档管理页面"),
    "analysis.view": ("工作空间", "个人分析页面"),
    "feedback.view": ("工作空间", "个人反馈页面"),
    "ai_logs.view": ("工作空间", "个人 AI 日志页面"),
    "kb.create": ("知识库", "创建私有知识库 / 导入"),
    "kb.team_create": ("知识库", "创建团队知识库"),
    "kb.team_manage": ("知识库", "管理团队知识库归属及授权"),
    "api.personal": ("API", "配置和使用个人 API"),
    "api.view": ("API", "团队 API 管理页面"),
    "api.manage": ("API", "创建、编辑、停用及删除团队 API"),
    "api.grant": ("API", "分配团队 API 使用权"),
    "api.stats": ("API", "查看团队 API 调用统计"),
    "users.view": ("用户", "查看用户列表和资料"),
    "users.create": ("用户", "创建账号"),
    "users.edit": ("用户", "编辑账号资料"),
    "users.disable": ("用户", "停用 / 启用账号"),
    "users.password": ("用户", "重置密码"),
    "users.sessions": ("用户", "查看登录会话及强制退出"),
    "users.roles": ("用户", "分配角色"),
    "roles.view": ("角色", "查看角色"),
    "roles.manage": ("角色", "创建、编辑及停用角色"),
    "system.view": ("系统", "查看系统设置"),
    "system.edit": ("系统", "修改系统设置"),
    "logs.view": ("系统日志", "查看及下载系统日志"),
    "logs.clear": ("系统日志", "清空系统日志"),
    "audit.view": ("操作日志", "查看操作日志"),
    "audit.clear": ("操作日志", "清理操作日志"),
}
MEMBER = [
    "documents.view",
    "analysis.view",
    "feedback.view",
    "ai_logs.view",
    "kb.create",
    "api.personal",
]
DEPENDENCIES = {
    **{
        p: "users.view" for p in CATALOG if p.startswith("users.") and p != "users.view"
    },
    "roles.manage": "roles.view",
    "api.manage": "api.view",
    "api.grant": "api.view",
    "api.stats": "api.view",
    "system.edit": "system.view",
    "audit.clear": "audit.view",
    "logs.clear": "logs.view",
}
KB_ACTIONS = ["query", "edit", "manage", "export", "download"]
LEVELS = {
    "reader": ["query"],
    "editor": ["query", "edit"],
    "manager": ["query", "edit", "manage"],
    "owner": KB_ACTIONS,
}


def init():
    with a.db.get_cursor() as cur:
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS permission_roles (
          id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '',
          permissions TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, protected INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS permission_user_roles (
          user_id TEXT NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
          role_id TEXT NOT NULL REFERENCES permission_roles(id), PRIMARY KEY(user_id,role_id));
        CREATE TABLE IF NOT EXISTS permission_profiles (
          user_id TEXT PRIMARY KEY REFERENCES auth_users(id) ON DELETE CASCADE,
          display_name TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', last_login INTEGER,
          password_reset_at INTEGER);
        CREATE TABLE IF NOT EXISTS permission_kbs (
          kb_id TEXT PRIMARY KEY REFERENCES kbs(id) ON DELETE CASCADE,
          scope TEXT NOT NULL DEFAULT 'private', enabled INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS permission_grants (
          kind TEXT NOT NULL, resource_id TEXT NOT NULL, subject_type TEXT NOT NULL,
          subject_id TEXT NOT NULL, effect TEXT NOT NULL, actions TEXT NOT NULL,
          PRIMARY KEY(kind,resource_id,subject_type,subject_id));
        CREATE TABLE IF NOT EXISTS permission_call_usage (log_id INTEGER PRIMARY KEY REFERENCES ai_call_logs(id) ON DELETE CASCADE, provider_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS permission_seed (id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS permission_state (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
        INSERT OR IGNORE INTO permission_state VALUES (1,1);
        """)
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("SELECT 1 FROM permission_seed WHERE id=1")
        seeded = bool(cur.fetchone())
        for rid, name, caps, protected in [
            ("super", "超级管理员", list(CATALOG), 1),
            ("admin", "系统管理员", list(CATALOG), 0),
            ("member", "普通成员", MEMBER, 0),
        ]:
            if seeded:
                continue
            cur.execute(
                "INSERT OR IGNORE INTO permission_roles(id,name,permissions,protected) VALUES (?,?,?,?)",
                (rid, name, json.dumps(caps), protected),
            )
        cur.execute("INSERT OR IGNORE INTO permission_seed VALUES (1)")
        cur.execute("SELECT 1 FROM permission_seed WHERE id=2")
        if not cur.fetchone():
            # Upgrade only the unchanged built-in administrator role, once.
            # Customized roles require explicit grants; revoked rights stay revoked.
            cur.execute("SELECT permissions FROM permission_roles WHERE id='admin'")
            admin = cur.fetchone()
            if admin and set(json.loads(admin["permissions"])) == set(CATALOG) - {"logs.view", "logs.clear"}:
                cur.execute("UPDATE permission_roles SET permissions=? WHERE id='admin'", (json.dumps(list(CATALOG)),))
            cur.execute("INSERT INTO permission_seed VALUES (2)")
            bump(cur)
        cur.execute(
            "UPDATE permission_roles SET permissions=?,enabled=1,protected=1 WHERE id='super'",
            (json.dumps(list(CATALOG)),),
        )
        # Profiles are migration markers. Empty role assignments remain empty on restart.
        cur.execute(
            "SELECT id,role FROM auth_users WHERE id NOT IN (SELECT user_id FROM permission_profiles) ORDER BY created_at,id"
        )
        old_users = cur.fetchall()
        cur.execute("SELECT 1 FROM permission_user_roles WHERE role_id='super' LIMIT 1")
        has_super = bool(cur.fetchone())
        for u in old_users:
            rid = "member"
            if u["role"] == "admin":
                rid = "admin" if has_super else "super"
                has_super = True
            cur.execute(
                "INSERT INTO permission_profiles(user_id) VALUES (?)", (u["id"],)
            )
            cur.execute(
                "INSERT INTO permission_user_roles VALUES (?,?)", (u["id"], rid)
            )
        cur.execute("INSERT OR IGNORE INTO permission_kbs(kb_id) SELECT id FROM kbs")
        # Consume legacy grants exactly once; compatibility routes also write the new table.
        cur.execute("SELECT * FROM auth_kb_grants")
        for g in cur.fetchall():
            cur.execute(
                "INSERT OR IGNORE INTO permission_grants VALUES ('kb',?,'user',?,'allow',?)",
                (g["kb_id"], g["user_id"], json.dumps(LEVELS.get(g["role"], []))),
            )
        cur.execute("SELECT * FROM auth_provider_grants")
        for g in cur.fetchall():
            cur.execute(
                "INSERT OR IGNORE INTO permission_grants VALUES ('api',?,'user',?,'allow','[\"use\"]')",
                (g["provider_id"], g["user_id"]),
            )
        cur.execute("DELETE FROM auth_kb_grants")
        cur.execute("DELETE FROM auth_provider_grants")
        cur.connection.commit()


def roles(uid):
    return a.rows(
        "SELECT r.* FROM permission_roles r JOIN permission_user_roles ur ON r.id=ur.role_id WHERE ur.user_id=? ORDER BY r.name",
        (uid,),
    )


def is_super(uid=None):
    uid = uid or a.user()["id"]
    return any(r["id"] == "super" and r["enabled"] for r in roles(uid))


def effective(uid=None):
    uid = uid or a.user()["id"]
    return {
        p
        for r in roles(uid)
        if r["enabled"]
        for p in json.loads(r["permissions"])
        if p in CATALOG
    }


def has(permission, uid=None):
    return permission in effective(uid)


def require(permission):
    u = a.user()
    if not has(permission, u["id"]):
        raise HTTPException(
            403, "没有此操作的权限：" + CATALOG.get(permission, ("", permission))[1]
        )
    return u


def revision():
    return a.rows("SELECT revision FROM permission_state WHERE id=1")[0]["revision"]


def bump(cur):
    cur.execute("UPDATE permission_state SET revision=revision+1 WHERE id=1")


def check_revision(cur, expected):
    cur.execute("SELECT revision FROM permission_state WHERE id=1")
    if expected is not None and cur.fetchone()["revision"] != expected:
        raise HTTPException(409, "权限已被其他操作修改，请刷新并重新预览")


def ceiling(caps, protected=False):
    if protected and not is_super():
        raise HTTPException(403, "只有超级管理员可以授予超级管理员角色")
    if not set(caps) <= effective():
        raise HTTPException(403, "不能授予或修改超出自己权限范围的权限")


def target_user(uid):
    found = a.rows("SELECT * FROM auth_users WHERE id=?", (uid,))
    if not found:
        raise HTTPException(404, "账号不存在")
    if is_super(uid) and not is_super():
        raise HTTPException(403, "不能管理超级管理员账号")
    ceiling(effective(uid))
    return found[0]


def validate_caps(caps):
    if set(caps) - CATALOG.keys():
        raise HTTPException(400, "包含未知权限项")
    missing = {
        DEPENDENCIES[c]
        for c in caps
        if c in DEPENDENCIES and DEPENDENCIES[c] not in caps
    }
    if missing:
        raise HTTPException(400, "请先启用关联页面权限：" + ", ".join(sorted(missing)))


def selected_roles(ids):
    if not ids:
        raise HTTPException(400, "至少分配一个角色")
    result = []
    for rid in set(ids):
        found = a.rows(
            "SELECT * FROM permission_roles WHERE id=? AND enabled=1", (rid,)
        )
        if not found:
            raise HTTPException(400, "角色不存在或已停用")
        ceiling(json.loads(found[0]["permissions"]), rid == "super")
        result.append(found[0])
    return result


def assert_super_survives(cur, uid, new_roles=None, disabled=None):
    if (new_roles is not None and "super" not in new_roles) or disabled:
        cur.execute(
            "SELECT 1 FROM permission_user_roles ur JOIN auth_users u ON u.id=ur.user_id WHERE ur.role_id='super' AND u.disabled=0 AND u.id<>? LIMIT 1",
            (uid,),
        )
        if not cur.fetchone():
            raise HTTPException(400, "必须保留至少一个启用的超级管理员")


def kb_policy(kid):
    found = a.rows("SELECT * FROM permission_kbs WHERE kb_id=?", (kid,))
    return found[0] if found else {"scope": "private", "enabled": 1}


def resource(
    kind,
    rid,
    uid=None,
    override=None,
    role_override=None,
    role_ids_override=None,
    disabled_override=None,
):
    """Explain all sources, then apply account/resource disable and user deny."""
    uid = uid or a.user()["id"]
    users = a.rows("SELECT disabled FROM auth_users WHERE id=?", (uid,))
    if not users:
        raise HTTPException(404, "账号不存在")
    owned = kind == "kb" and bool(
        a.rows(
            "SELECT 1 FROM auth_objects WHERE kind='kb' AND object_id=? AND owner_id=?",
            (rid, uid),
        )
    )
    if kind == "kb":
        exists = bool(a.rows("SELECT 1 FROM kbs WHERE id=?", (rid,)))
        active = kb_policy(rid)["enabled"]
    else:
        found = a.rows(
            "SELECT enabled FROM auth_providers WHERE id=? AND scope='team'", (rid,)
        )
        exists = bool(found)
        active = found[0]["enabled"] if found else False
    if not exists:
        raise HTTPException(404, "资源不存在")
    member_roles = roles(uid)
    if role_ids_override is not None:
        member_roles = [
            r
            for r in a.rows("SELECT * FROM permission_roles")
            if r["id"] in role_ids_override
        ]
    if disabled_override is not None:
        users[0]["disabled"] = disabled_override
    if role_override:
        member_roles = [
            role_override if r["id"] == role_override["id"] else r for r in member_roles
        ]
    role_map = {r["id"]: r["name"] for r in member_roles if r["enabled"]}
    grants = a.rows(
        "SELECT * FROM permission_grants WHERE kind=? AND resource_id=?", (kind, rid)
    )
    if override is not None:
        grants = [
            g
            for g in grants
            if (g["subject_type"], g["subject_id"])
            != (override["subject_type"], override["subject_id"])
        ]
        if override["effect"] != "remove":
            grants.append({**override, "actions": json.dumps(override["actions"])})
    sources = []
    allowed = set(KB_ACTIONS if owned else [])
    if owned:
        sources.append({"source": "所有者", "actions": KB_ACTIONS, "effect": "allow"})
    denied = False
    for g in grants:
        applies = (g["subject_type"] == "user" and g["subject_id"] == uid) or (
            g["subject_type"] == "role" and g["subject_id"] in role_map
        )
        if not applies:
            continue
        actions = json.loads(g["actions"])
        denied |= g["effect"] == "deny"
        if g["effect"] == "allow":
            allowed.update(actions)
        sources.append(
            {
                "source": "单独授权"
                if g["subject_type"] == "user"
                else "角色：" + role_map[g["subject_id"]],
                "effect": g["effect"],
                "actions": actions,
            }
        )
    reason = (
        "账号已停用"
        if users[0]["disabled"]
        else "资源已停用"
        if not active
        else "已明确禁止此用户访问"
        if denied
        else ""
    )
    return {
        "actions": sorted(allowed) if not reason else [],
        "sources": sources,
        "blocked": bool(reason),
        "reason": reason,
        "owner": owned,
    }


def require_resource(kind, rid, action):
    if action not in resource(kind, rid)["actions"]:
        raise HTTPException(403, "没有此资源的操作权限，历史会话仍可查看")


def manage_resource(kind, rid):
    if kind == "api":
        require("api.grant")
        if not a.rows(
            "SELECT 1 FROM auth_providers WHERE id=? AND scope='team'", (rid,)
        ):
            raise HTTPException(404, "团队 API 不存在")
        return
    if not a.rows("SELECT 1 FROM kbs WHERE id=?", (rid,)):
        raise HTTPException(404, "知识库不存在")
    if a.owns("kb", rid):
        return
    if kb_policy(rid)["scope"] == "team" and has("kb.team_manage"):
        return
    require_resource("kb", rid, "manage")


def grant_check(kind, rid, subject_type, subject_id, effect, actions):
    manage_resource(kind, rid)
    if subject_type == "role":
        if effect == "deny":
            raise HTTPException(400, "明确禁止只针对指定用户")
        if not a.rows("SELECT 1 FROM permission_roles WHERE id=?", (subject_id,)):
            raise HTTPException(404, "角色不存在")
    elif not a.rows("SELECT 1 FROM auth_users WHERE id=?", (subject_id,)):
        raise HTTPException(404, "用户不存在")
    if kind == "kb":
        if subject_type == "user" and a.rows(
            "SELECT 1 FROM auth_objects WHERE kind='kb' AND object_id=? AND owner_id=?",
            (rid, subject_id),
        ):
            raise HTTPException(400, "不能修改所有者权限，请先转移归属")
        if set(actions) - set(KB_ACTIONS):
            raise HTTPException(400, "未知知识库权限")
        mine = resource("kb", rid)
        global_manager = kb_policy(rid)["scope"] == "team" and has("kb.team_manage")
        old = a.rows(
            "SELECT actions FROM permission_grants WHERE kind=? AND resource_id=? AND subject_type=? AND subject_id=?",
            (kind, rid, subject_type, subject_id),
        )
        changing_manage = "manage" in actions or any(
            "manage" in json.loads(g["actions"]) for g in old
        )
        if not mine["owner"] and not global_manager:
            if changing_manage or not set(actions) <= set(mine["actions"]):
                raise HTTPException(
                    403,
                    "只能授权自己拥有的查询、编辑、下载或导出权限；不能任命其他管理者",
                )
        if "manage" in actions and not {"query", "edit"} <= set(actions):
            raise HTTPException(400, "管理权限需要查询及编辑权限")
        if "edit" in actions and "query" not in actions:
            raise HTTPException(400, "编辑权限需要查询权限")
    elif set(actions) - {"use"}:
        raise HTTPException(400, "未知 API 权限")


def write_grant(kind, rid, change, expected=None):
    with a.db.get_cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        check_revision(cur, expected)
        grant_check(kind, rid, **change)
        args = (kind, rid, change["subject_type"], change["subject_id"])
        if change["effect"] == "remove":
            cur.execute(
                "DELETE FROM permission_grants WHERE kind=? AND resource_id=? AND subject_type=? AND subject_id=?",
                args,
            )
        else:
            cur.execute(
                "INSERT INTO permission_grants VALUES (?,?,?,?,?,?) ON CONFLICT(kind,resource_id,subject_type,subject_id) DO UPDATE SET effect=excluded.effect,actions=excluded.actions",
                (*args, change["effect"], json.dumps(change["actions"])),
            )
        bump(cur)
        cur.connection.commit()
    a.audit(
        "resource_grant_" + change["effect"],
        json.dumps({"kind": kind, "resource": rid, **change}, ensure_ascii=False),
    )


def visible_kbs(uid=None):
    uid = uid or a.user()["id"]
    return [
        r["id"]
        for r in a.rows("SELECT id FROM kbs")
        if "query" in resource("kb", r["id"], uid)["actions"]
    ]
