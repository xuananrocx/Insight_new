# ruff: noqa: F811
"""Member-directory visibility tiers and role member_scope extensions."""

import json

from src.core import accounts as a
from src.core import permissions as p
from tests.test_accounts_access import env, kb  # noqa: F401
from tests.test_permissions import assign, grant


def mkrole(client, name, scope=None):
    body = {"name": name, "permissions": []}
    if scope is not None:
        body["member_scope"] = scope
    result = client.post("/api/v1/admin/roles", json=body)
    assert result.status_code == 201, result.text
    return result.json()["id"]


def put_role(client, rid, body):
    revision = client.get("/api/v1/permissions/catalog").json()["revision"]
    result = client.put(
        f"/api/v1/admin/roles/{rid}", json={**body, "expected_revision": revision}
    )
    assert result.status_code == 200, result.text


def new_user(env, name):
    result = env.admin.post(
        "/api/v1/admin/users", json={"username": name, "password": "password-123456"}
    )
    assert result.status_code == 201, result.text
    return result.json()["id"]


def member_ids(client):
    return {u["id"] for u in client.get("/api/v1/members").json()}


def directory_ids(client):
    return {r["id"] for r in client.get("/api/v1/roles/directory").json()}


def test_two_tier_directory_and_super_hidden(env):
    # Members share the built-in role; super stays hidden from non-super viewers.
    ids = member_ids(env.alice)
    assert env.bob_id in ids
    assert env.admin_id not in ids
    assert member_ids(env.admin) >= {env.alice_id, env.bob_id}
    assert env.admin_id not in member_ids(env.admin)  # self never listed
    assert directory_ids(env.alice) == {"member"}
    assert {"member", "admin", "super"} <= directory_ids(env.admin)


def test_no_shared_role_means_invisible(env):
    groupa = mkrole(env.admin, "vis-a")
    groupb = mkrole(env.admin, "vis-b")
    assign(env, env.alice_id, [groupa])
    assign(env, env.bob_id, [groupb])
    assert member_ids(env.alice) == set()
    assert member_ids(env.bob) == set()
    assert directory_ids(env.alice) == {groupa}


def test_member_scope_extends_members_and_directory(env):
    groupa = mkrole(env.admin, "scope-a")
    groupb = mkrole(env.admin, "scope-b")
    assign(env, env.alice_id, ["member", groupa])
    assign(env, env.bob_id, ["member", groupb])
    put_role(
        env.admin,
        groupa,
        {
            "name": "scope-a",
            "permissions": [],
            "member_scope": {"roles": [groupb], "users": [env.admin_id]},
        },
    )
    ids = member_ids(env.alice)
    assert env.bob_id in ids
    assert env.admin_id not in ids  # super stays hidden even when named directly
    assert groupb in directory_ids(env.alice)


def test_scope_is_not_transitive(env):
    a1 = mkrole(env.admin, "chain-a")
    b1 = mkrole(env.admin, "chain-b")
    c1 = mkrole(env.admin, "chain-c")
    carol = new_user(env, "carol")
    assign(env, env.alice_id, [a1])
    assign(env, env.bob_id, [b1])
    assign(env, carol, [c1])
    put_role(
        env.admin,
        b1,
        {"name": "chain-b", "permissions": [], "member_scope": {"roles": [c1]}},
    )
    put_role(
        env.admin,
        a1,
        {"name": "chain-a", "permissions": [], "member_scope": {"roles": [b1]}},
    )
    ids = member_ids(env.alice)
    assert env.bob_id in ids
    assert carol not in ids
    assert carol in member_ids(env.bob)


def test_disabled_own_role_hides_members(env):
    group = mkrole(env.admin, "off-a")
    assign(env, env.alice_id, [group])
    assign(env, env.bob_id, ["member", group])
    assert env.bob_id in member_ids(env.alice)
    put_role(env.admin, group, {"name": "off-a", "permissions": [], "enabled": False})
    assert env.bob_id not in member_ids(env.alice)
    assert group not in directory_ids(env.bob)


def test_dangling_scope_references_are_ignored(env):
    groupa = mkrole(env.admin, "dangling-a")
    doomed = mkrole(env.admin, "dangling-b")
    assign(env, env.alice_id, [groupa])
    put_role(
        env.admin,
        groupa,
        {"name": "dangling-a", "permissions": [], "member_scope": {"roles": [doomed]}},
    )
    result = env.admin.delete(f"/api/v1/admin/roles/{doomed}")
    assert result.status_code == 200, result.text
    assert env.alice.get("/api/v1/members").status_code == 200
    assert member_ids(env.alice) == set()
    # Corrupted/ghost user references written outside the API also stay harmless.
    a.execute(
        "UPDATE permission_roles SET member_scope=? WHERE id=?",
        (json.dumps({"roles": [], "users": ["u_ghost"]}), groupa),
    )
    assert env.alice.get("/api/v1/members").status_code == 200


def test_scope_validation_rejects_unknown_or_super(env):
    assert (
        env.admin.post(
            "/api/v1/admin/roles",
            json={"name": "bad-1", "permissions": [], "member_scope": {"roles": ["nope"]}},
        ).status_code
        == 400
    )
    assert (
        env.admin.post(
            "/api/v1/admin/roles",
            json={"name": "bad-2", "permissions": [], "member_scope": {"roles": ["super"]}},
        ).status_code
        == 400
    )
    assert (
        env.admin.post(
            "/api/v1/admin/roles",
            json={"name": "bad-3", "permissions": [], "member_scope": {"users": ["u_ghost"]}},
        ).status_code
        == 400
    )


def test_update_without_scope_keeps_existing_value(env):
    groupa = mkrole(env.admin, "keep-a")
    groupb = mkrole(env.admin, "keep-b")
    assign(env, env.alice_id, [groupa])
    assign(env, env.bob_id, [groupb])
    put_role(
        env.admin,
        groupa,
        {"name": "keep-a", "permissions": [], "member_scope": {"roles": [groupb]}},
    )
    put_role(env.admin, groupa, {"name": "keep-a", "permissions": [], "description": "x"})
    assert env.bob_id in member_ids(env.alice)
    put_role(
        env.admin,
        groupa,
        {"name": "keep-a", "permissions": [], "member_scope": {"roles": [], "users": []}},
    )
    assert env.bob_id not in member_ids(env.alice)


def test_role_payload_and_legacy_kb_members_endpoint(env):
    groupa = mkrole(env.admin, "payload-a")
    groupb = mkrole(env.admin, "payload-b")
    put_role(
        env.admin,
        groupa,
        {
            "name": "payload-a",
            "permissions": [],
            "member_scope": {"roles": [groupb], "users": [env.bob_id]},
        },
    )
    payload = next(
        r for r in env.admin.get("/api/v1/admin/roles").json() if r["id"] == groupa
    )
    assert payload["member_scope"] == {"roles": [groupb], "users": [env.bob_id]}
    kid = kb(env.alice)
    grant(env.alice, "kb", kid, env.bob_id, ["query"])
    grant(env.alice, "kb", kid, env.admin_id, ["query"])
    rows = env.alice.get(f"/api/v1/kbs/{kid}/members").json()
    listed = {u["id"] for u in rows}
    assert env.bob_id in listed
    assert env.admin_id not in listed  # granted but invisible


def test_member_scope_column_migration(env):
    a.execute("ALTER TABLE permission_roles DROP COLUMN member_scope")
    a.execute("DELETE FROM permission_seed WHERE id=4")
    p.init()
    with a.db.get_cursor() as cur:
        cols = {row[1] for row in cur.execute("PRAGMA table_info(permission_roles)")}
    assert "member_scope" in cols
    assert env.alice.get("/api/v1/members").status_code == 200
