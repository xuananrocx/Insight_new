# ruff: noqa: F811
"""System files and audit records have independent permissions and cleanup."""
import io
import json
import logging
import zipfile

from src.core import accounts as a, logging_config, permissions as p
from tests.test_accounts_access import env, actor  # noqa: F401
from tests.test_permissions import role, assign


def log_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(logging_config, "LOG_DIR", tmp_path)
    monkeypatch.setattr(logging_config, "LOG_FILE", tmp_path / "app.log")
    return logging_config.LOG_FILE


def test_file_tail_download_and_independent_cleanup(env, monkeypatch, tmp_path):
    path = log_directory(monkeypatch, tmp_path)
    lines = [f"INFO 测试运行记录 {i}" for i in range(1800)] + ["ERROR 系统异常", "Traceback: 错误堆栈"]
    path.write_text("\n".join(lines), encoding="utf-8")
    (tmp_path / "app.log.1").write_text("轮转日志", encoding="utf-8")
    (tmp_path / "unrelated.txt").write_text("keep")
    assert env.admin.get("/api/v1/logs/tail?lines=500").json()["lines"] == lines[-500:]
    assert env.admin.get("/api/v1/logs/tail?lines=0").status_code == 422
    info = env.admin.get("/api/v1/logs/info").json()
    assert {f["name"] for f in info["files"]} == {"app.log", "app.log.1"}
    assert info["newest_mtime"] and info["total_size"] > 0
    response = env.admin.get("/api/v1/logs/download")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"app.log", "app.log.1"}
        assert archive.read("app.log") == path.read_bytes()

    before = path.read_bytes()
    assert env.admin.delete("/api/v1/admin/audit").status_code == 204
    assert path.read_bytes() == before
    assert (tmp_path / "app.log.1").exists()
    audit_before = a.rows("SELECT * FROM auth_audit")
    assert len(audit_before) == 1 and audit_before[0]["action"] == "audit_cleared"

    # Exercise the active Windows file handle, not just an unopened fixture file.
    handler = logging.FileHandler(path, encoding="utf-8")
    logging.getLogger().addHandler(handler)
    try:
        assert env.admin.delete("/api/v1/logs").status_code == 204
        assert "测试运行记录" not in path.read_text(encoding="utf-8")
        assert "Traceback" not in path.read_text(encoding="utf-8")
        handler.handle(logging.makeLogRecord({"msg": "清空后继续记录", "levelno": logging.INFO}))
        handler.flush()
        assert "清空后继续记录" in path.read_text(encoding="utf-8")
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
    assert not (tmp_path / "app.log.1").exists()
    assert (tmp_path / "unrelated.txt").read_text() == "keep"
    assert a.rows("SELECT * FROM auth_audit") == audit_before


def test_log_permissions_are_independent_and_read_only(env, monkeypatch, tmp_path):
    path = log_directory(monkeypatch, tmp_path)
    path.write_text("runtime", encoding="utf-8")
    for client in (env.alice, env.bob):
        assert client.get("/api/v1/logs/tail").status_code == 403
        assert client.get("/api/v1/admin/audit").status_code == 403
    audit_role = role(env.admin, ["audit.view", "audit.clear"])
    viewer_role = role(env.admin, ["logs.view"])
    assign(env, env.alice_id, [audit_role])
    assign(env, env.bob_id, [viewer_role])
    for suffix in ("info", "tail", "download"):
        assert env.alice.get(f"/api/v1/logs/{suffix}").status_code == 403
        assert env.bob.get(f"/api/v1/logs/{suffix}").status_code == 200
    assert env.alice.delete("/api/v1/logs").status_code == 403
    assert env.bob.delete("/api/v1/logs").status_code == 403
    assert env.bob.get("/api/v1/admin/audit").status_code == 403
    assert env.bob.delete("/api/v1/admin/audit").status_code == 403
    assert env.bob.get("/api/v1/settings/log_level").status_code == 200
    assert env.bob.put("/api/v1/settings/log_level", json={"level": "DEBUG"}).status_code == 403
    assert env.alice.delete("/api/v1/admin/audit").status_code == 204
    assert path.read_text(encoding="utf-8") == "runtime"
    system_role = role(env.admin, ["logs.view", "logs.clear"])
    assign(env, env.bob_id, [system_role])
    assert env.bob.delete("/api/v1/logs").status_code == 204
    assert env.bob.delete("/api/v1/admin/audit").status_code == 403


def test_audit_chinese_details_and_legacy_events(env):
    rid = role(env.admin, ["logs.view"], "日志观察员")
    with actor(env.admin_id):
        a.audit("user_updated", json.dumps({"id": env.alice_id, "changes": {
            "role_ids": [rid], "disabled": True, "password_reset": True, "display_name": "测试用户"}}))
        a.audit("role_updated", json.dumps({"id": rid, "before": ["audit.view"], "after": ["logs.view"], "enabled": True}))
        a.audit("resource_grant_deny", json.dumps({"kind": "api", "resource": "deleted-api", "subject_type": "user", "subject_id": env.alice_id, "effect": "deny", "actions": []}))
        a.audit("system_configuration_changed", "PUT /api/v1/settings/log_level")
        a.audit("provider_deleted", "old-provider-id")
        a.audit("future_event", "legacy non-json detail")
    response = env.admin.get("/api/v1/admin/audit")
    assert response.status_code == 200, response.text
    records = {r["action"]: r for r in response.json()}
    assert records["user_created"]["action_label"] == "创建账号"
    details = "\n".join(records["user_updated"]["details"])
    for text in ("alice", "分配角色", "日志观察员", "账号状态：停用", "重置密码：是", "显示名称：测试用户"):
        assert text in details
    assert "查看及下载系统日志" in "\n".join(records["role_updated"]["details"])
    assert "授权规则：明确禁止" in records["resource_grant_deny"]["details"]
    assert "配置项：系统日志级别" in records["system_configuration_changed"]["details"]
    assert "已删除或不可用" in records["provider_deleted"]["details"][0]
    assert records["future_event"]["details"] == ["操作详情：legacy non-json detail"]
    assert "initial-member-password" not in response.text


def test_system_log_permission_upgrade_is_once_and_preserves_customization(env):
    original = a.rows("SELECT permissions FROM permission_roles WHERE id='admin'")[0]["permissions"]
    legacy = list(set(p.CATALOG) - {"logs.view", "logs.clear"})
    try:
        a.execute("UPDATE permission_roles SET permissions=? WHERE id='admin'", (json.dumps(legacy),))
        a.execute("DELETE FROM permission_seed WHERE id=2")
        p.init()
        assert set(json.loads(a.rows("SELECT permissions FROM permission_roles WHERE id='admin'")[0]["permissions"])) == set(p.CATALOG)
        a.execute("UPDATE permission_roles SET permissions=? WHERE id='admin'", (json.dumps(legacy),))
        p.init()  # Explicitly revoked log rights are not restored on the next startup.
        assert "logs.view" not in json.loads(a.rows("SELECT permissions FROM permission_roles WHERE id='admin'")[0]["permissions"])
        a.execute("UPDATE permission_roles SET permissions='[\"audit.view\"]' WHERE id='admin'")
        a.execute("DELETE FROM permission_seed WHERE id=2")
        p.init()
        assert json.loads(a.rows("SELECT permissions FROM permission_roles WHERE id='admin'")[0]["permissions"]) == ["audit.view"]
    finally:
        a.execute("UPDATE permission_roles SET permissions=? WHERE id='admin'", (original,))
