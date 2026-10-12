# ruff: noqa: F811
"""两层默认知识库：个人层优先于系统层，解析限定账号可见域并带降级原因。"""

import pytest

from src.core import accounts as a
from src.core.config import settings
from tests.test_accounts_access import env, kb  # noqa: F401


def get_default(client):
    r = client.get("/api/v1/settings/default_kb")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def clean(env):
    env.admin.put("/api/v1/settings/default_kb/system", json={"kb_id": None})
    a.execute("UPDATE auth_users SET default_kb_id=NULL")
    settings.config.pop("ui", None)
    return env


def test_auth_users_column_migration(env):
    a.init_auth()
    a.init_auth()  # 幂等：列已存在时不报错
    from src.db import metadata_db as db

    with db.get_cursor() as cur:
        cols = {r[1] for r in cur.execute("PRAGMA table_info(auth_users)")}
    assert "default_kb_id" in cols


def test_builtin_without_any_config(clean):
    env = clean
    # 超级管理员可见全部：未配置时解析到内置库
    info = get_default(env.admin)
    assert info["source"] == "builtin"
    assert info["kb_id"]
    assert info["personal_kb_id"] is None
    assert info["system_kb_id"] is None
    assert info["fallback_reason"] is None
    # 普通成员对内置库无可见权限且未被授权任何库 → 无默认（前端需手动选择）
    a.execute("DELETE FROM auth_kb_grants")
    info = get_default(env.alice)
    assert info["kb_id"] is None
    assert info["source"] == "first"


def test_system_layer_and_personal_priority(clean):
    env = clean
    kid_a = kb(env.admin, "SharedA")
    assert (
        env.admin.put(
            f"/api/v1/kbs/{kid_a}/members/{env.alice_id}", json={"role": "reader"}
        ).status_code
        == 200
    )
    r = env.admin.put("/api/v1/settings/default_kb/system", json={"kb_id": kid_a})
    assert r.status_code == 200, r.text

    # alice 未设置个人默认 → 系统层生效
    info = get_default(env.alice)
    assert (info["source"], info["kb_id"], info["system_kb_id"]) == (
        "system",
        kid_a,
        kid_a,
    )

    # alice 设置个人默认 → 优先于系统层；admin 仍走系统层
    kid_b = kb(env.alice, "MineB")
    r = env.alice.put("/api/v1/settings/default_kb", json={"kb_id": kid_b})
    assert r.status_code == 200, r.text
    info = get_default(env.alice)
    assert (info["source"], info["kb_id"], info["personal_kb_id"]) == (
        "personal",
        kid_b,
        kid_b,
    )
    admin_info = get_default(env.admin)
    assert (admin_info["source"], admin_info["kb_id"]) == ("system", kid_a)


def test_unavailable_layers_fall_back_with_reason(clean):
    env = clean
    kid_b = kb(env.alice, "Vanish")
    assert (
        env.alice.put("/api/v1/settings/default_kb", json={"kb_id": kid_b}).status_code
        == 200
    )
    # 个人默认库被删除 → 降级到内置，原始配置保留并给出原因
    assert env.alice.delete(f"/api/v1/kbs/{kid_b}").status_code in (200, 204)
    info = get_default(env.alice)
    assert info["source"] in ("builtin", "first")
    assert info["personal_kb_id"] == kid_b
    assert info["fallback_reason"] == "personal_unavailable"

    # 系统层指向成员不可见的库 → 同样降级
    kid_secret = kb(env.admin, "AdminOnly")
    assert (
        env.admin.put(
            "/api/v1/settings/default_kb/system", json={"kb_id": kid_secret}
        ).status_code
        == 200
    )
    info = get_default(env.bob)
    assert info["source"] in ("builtin", "first")
    assert info["system_kb_id"] == kid_secret
    assert info["fallback_reason"] == "system_unavailable"


def test_permission_guards(clean):
    env = clean
    kid_private = kb(env.admin, "GuardOnly")
    # 普通成员不能改系统层
    assert (
        env.alice.put(
            "/api/v1/settings/default_kb/system", json={"kb_id": kid_private}
        ).status_code
        == 403
    )
    # 个人层只能选自己可见的库
    assert (
        env.alice.put(
            "/api/v1/settings/default_kb", json={"kb_id": kid_private}
        ).status_code
        == 403
    )
    # 不存在的库
    assert (
        env.alice.put(
            "/api/v1/settings/default_kb", json={"kb_id": "does-not-exist"}
        ).status_code
        == 404
    )
