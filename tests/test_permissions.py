# ruff: noqa: F811
"""Behavioral RBAC tests through authenticated HTTP requests; models stay stubbed."""

import uuid

import pytest
from fastapi import HTTPException
from src.core import accounts as a
from src.db import metadata_db as db
from tests.test_accounts_access import env, actor, kb, session, provider  # noqa: F401


def role(client, caps, name=None):
    result = client.post(
        "/api/v1/admin/roles",
        json={"name": name or uuid.uuid4().hex, "permissions": caps},
    )
    assert result.status_code == 201, result.text
    return result.json()["id"]


def assign(env, uid, ids):
    result = env.admin.patch(f"/api/v1/admin/users/{uid}", json={"role_ids": ids})
    assert result.status_code == 200, result.text


def grant(client, kind, rid, subject, actions, subject_type="user", effect="allow"):
    body = dict(
        subject_type=subject_type, subject_id=subject, actions=actions, effect=effect
    )
    path = f"/api/v1/access/{kind}/{rid}"
    preview = client.post(path + "/preview", json=body)
    assert preview.status_code == 200, preview.text
    response = client.put(
        path, json={**body, "expected_revision": preview.json()["revision"]}
    )
    assert response.status_code == 200, response.text
    return preview.json()


def test_role_union_menu_api_and_delegation_ceiling(env):
    viewer = role(env.admin, ["users.view"])
    role_editor = role(
        env.admin, ["roles.view", "roles.manage", "users.view", "users.roles"]
    )
    assign(env, env.alice_id, [viewer, role_editor])
    # Changing roles does not invalidate login, but immediately changes backend capabilities.
    me = env.alice.get("/api/v1/auth/me").json()
    assert {r["id"] for r in me["roles"]} == {viewer, role_editor}
    assert env.alice.get("/api/v1/admin/users").status_code == 200
    assert env.alice.get("/api/v1/knowledge/files").status_code == 403
    assert env.alice.get("/api/v1/ai_logs").status_code == 403
    assert (
        env.alice.post(
            "/api/v1/providers",
            json={"name": "x", "base_url": "http://localhost", "chat_model": "x"},
        ).status_code
        == 403
    )
    assert (
        env.alice.post(
            "/api/v1/admin/roles",
            json={"name": "escape", "permissions": ["api.personal"]},
        ).status_code
        == 403
    )
    assert (
        env.alice.patch(
            f"/api/v1/admin/users/{env.bob_id}", json={"role_ids": ["super"]}
        ).status_code
        == 403
    )
    assert (
        env.alice.patch(
            f"/api/v1/admin/users/{env.admin_id}",
            json={"password": "reset-super-password"},
        ).status_code
        == 403
    )
    assert (
        env.admin.put(
            "/api/v1/admin/roles/super",
            json={"name": "changed", "permissions": [], "enabled": False},
        ).status_code
        == 400
    )
    # No implicit re-add of member role when restarting.
    a.init_auth()
    assert {r["id"] for r in env.alice.get("/api/v1/auth/me").json()["roles"]} == {
        viewer,
        role_editor,
    }


def test_resource_union_deny_preview_revoke_and_history(env):
    kid = kb(env.alice)
    shared = role(env.admin, [])
    assign(env, env.bob_id, ["member", shared])
    grant(env.alice, "kb", kid, shared, ["query"], "role")
    grant(env.alice, "kb", kid, env.bob_id, ["query", "edit"])
    sid = session(env.bob, kid)
    preview = grant(env.alice, "kb", kid, env.bob_id, [], effect="remove")
    assert preview["changes"][0]["after"] == ["query"]
    assert preview["changes"][0]["removed"] == ["edit"]
    assert env.bob.get(f"/api/v1/kbs/{kid}").status_code == 200
    grant(env.alice, "kb", kid, env.bob_id, [], effect="deny")
    assert env.bob.get(f"/api/v1/kbs/{kid}").status_code == 403
    assert env.bob.get(f"/api/v1/sessions/{sid}").status_code == 200
    assert (
        env.bob.post(
            "/api/v1/qa/ask_stream",
            json={
                "question": "q",
                "kb_scope": kid,
                "session_id": sid,
                "mode": "deep_ai",
            },
        ).status_code
        == 403
    )
    grant(env.alice, "kb", kid, env.bob_id, [], effect="remove")
    assert env.bob.get(f"/api/v1/kbs/{kid}").status_code == 200
    # Role disabling removes resource grants too, preview reports that impact.
    body = {"name": shared, "permissions": [], "enabled": False}
    preview = env.admin.post(f"/api/v1/admin/roles/{shared}/preview", json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["changes"][0]["resources"][0]["after"] == []
    result = env.admin.put(
        f"/api/v1/admin/roles/{shared}",
        json={**body, "expected_revision": preview.json()["revision"]},
    )
    assert result.status_code == 200, result.text
    assert env.bob.get(f"/api/v1/kbs/{kid}").status_code == 403


def test_team_api_management_is_not_usage_and_personal_stays_private(env):
    team = provider(env.admin, "team")
    own = provider(env.alice)
    assert (
        env.admin.put(
            "/api/v1/account/provider", json={"provider_id": team}
        ).status_code
        == 403
    )
    api_manager = role(env.admin, ["api.view", "api.manage"])
    assign(env, env.bob_id, [api_manager])
    item = next(
        r for r in env.bob.get("/api/v1/providers").json()["items"] if r["id"] == team
    )
    assert item["manageable"] and not item["usable"] and not item["grantable"]
    assert (
        env.bob.put(f"/api/v1/providers/{team}/members/{env.bob_id}").status_code == 403
    )
    assert env.bob.get("/api/v1/admin/api-stats").status_code == 403
    assert own not in [
        r["id"] for r in env.admin.get("/api/v1/providers").json()["items"]
    ]
    grant(env.admin, "api", team, api_manager, ["use"], "role")
    assert (
        env.bob.put("/api/v1/account/provider", json={"provider_id": team}).status_code
        == 200
    )
    with actor(env.bob_id):
        db.insert_ai_call_log(
            provider="team",
            provider_id=team,
            model="m",
            scene="test",
            messages=[{"role": "user", "content": "private question"}],
        )
    stats = env.admin.get("/api/v1/admin/api-stats")
    assert stats.status_code == 200, stats.text
    assert next(r for r in stats.json() if r["id"] == team)["calls"] == 1
    assert "private question" not in stats.text
    grant(env.admin, "api", team, env.bob_id, [], effect="deny")
    with actor(env.bob_id), pytest.raises(HTTPException):
        a.resolve_provider(team)


def test_team_management_private_conversion_disabled_owner_and_transfer(env):
    private = kb(env.alice)
    assert private not in [
        r["id"] for r in env.admin.get("/api/v1/access/resources").json()
    ]
    assert (
        env.admin.put(
            f"/api/v1/access/kb/{private}/policy", json={"scope": "team"}
        ).status_code
        == 403
    )
    team_creator = role(env.admin, ["kb.team_create"])
    assign(env, env.alice_id, ["member", team_creator])
    assert (
        env.alice.put(
            f"/api/v1/access/kb/{private}/policy", json={"scope": "team"}
        ).status_code
        == 200
    )
    assert private in [
        r["id"] for r in env.admin.get("/api/v1/access/resources").json()
    ]
    assert env.admin.get(f"/api/v1/kbs/{private}").status_code == 403
    # Team admin can manage disabled resources, without implicit content access.
    assert (
        env.admin.put(
            f"/api/v1/access/kb/{private}/policy", json={"enabled": False}
        ).status_code
        == 200
    )
    assert env.alice.get(f"/api/v1/kbs/{private}").status_code == 403
    assert private in [
        r["id"] for r in env.alice.get("/api/v1/access/resources").json()
    ]
    assert (
        env.alice.put(
            f"/api/v1/access/kb/{private}/policy", json={"enabled": True}
        ).status_code
        == 200
    )
    assert (
        env.admin.put(
            f"/api/v1/access/kb/{private}/policy", json={"owner_id": env.bob_id}
        ).status_code
        == 200
    )
    assert env.bob.get(f"/api/v1/kbs/{private}").json()["role"] == "owner"
    assert env.alice.get(f"/api/v1/kbs/{private}").status_code == 403


def test_separate_download_export_and_stale_preview(env, monkeypatch):
    kid = kb(env.alice)
    grant(env.alice, "kb", kid, env.bob_id, ["query", "edit", "manage"])
    base = a.settings.feed_folder / kid
    base.mkdir(parents=True, exist_ok=True)
    source = base / "sample.txt"
    source.write_text("owned content")
    db.upsert_file(
        relative_path="sample.txt",
        absolute_path=str(source),
        content_hash="hash",
        file_size=13,
        file_type="txt",
        kb_id=kid,
    )
    fid = db.get_file_by_path("sample.txt", kb_id=kid)["id"]
    path = f"/api/v1/knowledge/download/{fid}?kb_id={kid}"
    assert env.bob.get(path).status_code == 403
    assert env.bob.get(f"/api/v1/kbs/{kid}/export").status_code == 403
    grant(env.alice, "kb", kid, env.bob_id, ["query", "download"])
    assert env.bob.get(path).text == "owned content"
    assert env.bob.get(f"/api/v1/kbs/{kid}/export").status_code == 403
    # Stored paths outside the KB root are not downloadable even when granted.
    db.upsert_file(
        relative_path="escape.txt",
        absolute_path=str(a.USER_DATA_DIR / "credentials.key"),
        content_hash="hash",
        file_size=10,
        file_type="txt",
        kb_id=kid,
    )
    escape = db.get_file_by_path("escape.txt", kb_id=kid)["id"]
    assert (
        env.alice.get(f"/api/v1/knowledge/download/{escape}?kb_id={kid}").status_code
        == 404
    )
    body = {
        "subject_type": "user",
        "subject_id": env.bob_id,
        "effect": "remove",
        "actions": [],
    }
    preview = env.alice.post(f"/api/v1/access/kb/{kid}/preview", json=body).json()
    role(env.admin, [])
    assert (
        env.alice.put(
            f"/api/v1/access/kb/{kid}",
            json={**body, "expected_revision": preview["revision"]},
        ).status_code
        == 409
    )
    # Legacy migration consumes old grants; subsequent startup cannot resurrect revoked grants.
    a.execute("INSERT INTO auth_kb_grants VALUES (?,?,?)", (kid, env.bob_id, "reader"))
    a.init_auth()
    grant(env.alice, "kb", kid, env.bob_id, [], effect="remove")
    a.init_auth()
    assert env.bob.get(f"/api/v1/kbs/{kid}").status_code == 403


def test_global_summary_requires_resource_management(env, monkeypatch):
    from types import SimpleNamespace
    from src.api import routes_kbs
    from src.knowledge import ai_summarizer

    kid = kb(env.alice)
    path = f"/api/v1/kbs/{kid}/global_summary"
    db.update_kb_global_summary(kid, "original overview", "test", 10)
    builds = []

    def build(kb_id, force=False):
        builds.append((kb_id, force))
        db.update_kb_global_summary(kb_id, "updated overview", "test", 12)
        return SimpleNamespace(ok=True, summary="updated overview", model="test", tokens=12, doc_count=1)

    monkeypatch.setattr(ai_summarizer, "generate_kb_global_summary", build)
    # Match the reported case: access comes from the ordinary member role.
    grant(env.alice, "kb", kid, "member", ["query", "download"], "role")
    assert env.bob.get(path).json()["summary"] == "original overview"
    for client in (env.bob, env.admin):
        assert client.delete(path).status_code == 403
        assert client.post(path + "/build?force=true").status_code == 403
    # Handler-level checks also hold if a future route bypasses the middleware.
    with actor(env.bob_id):
        for operation in (routes_kbs.delete_kb_global_summary, routes_kbs.build_kb_global_summary):
            with pytest.raises(HTTPException) as denied:
                operation(kid)
            assert denied.value.status_code == 403
    assert not builds
    assert db.get_kb_global_summary(kid)["summary"] == "original overview"

    grant(env.alice, "kb", kid, env.bob_id, ["query", "edit"])
    assert env.bob.delete(path).status_code == 403
    assert env.bob.post(path + "/build").status_code == 403
    grant(env.alice, "kb", kid, env.bob_id, ["query", "edit", "manage"])
    assert env.bob.post(path + "/build?force=true").status_code == 200
    assert builds == [(kid, True)]
    assert env.bob.delete(path).status_code == 200
    assert db.get_kb_global_summary(kid) is None
    assert env.alice.post(path + "/build").status_code == 200
    grant(env.alice, "kb", kid, env.bob_id, [], effect="remove")
    assert env.bob.get(path).status_code == 200
    assert env.bob.delete(path).status_code == 403
    assert env.bob.post(path + "/build").status_code == 403
    assert db.get_kb_global_summary(kid)["summary"] == "updated overview"
    assert env.alice.delete(path).status_code == 200


def test_all_question_modes_are_available_without_feature_roles(env, monkeypatch):
    from src.api import routes_qa

    kid = kb(env.alice)
    team = provider(env.admin, "team")
    grant(env.alice, "kb", kid, env.bob_id, ["query"])
    grant(env.admin, "api", team, env.bob_id, ["use"])
    empty = role(env.admin, [])
    assign(env, env.bob_id, [empty])

    async def fake_stream(**kwargs):
        yield {"type": "done", "data": {"answer": "ok", "mode": kwargs["mode"]}}

    monkeypatch.setattr(routes_qa, "ask_stream", fake_stream)
    for mode in ["basic", "deep", "ai", "deep_ai"]:
        response = env.bob.post(
            "/api/v1/qa/ask_stream",
            json={"question": "q", "kb_scope": kid, "mode": mode, "provider_id": team},
        )
        assert response.status_code == 200, response.text
        assert '"answer": "ok"' in response.text
    # System prompt testing cannot bypass the default KB's resource authorization.
    viewer = role(env.admin, ["system.view", "system.edit"])
    assign(env, env.bob_id, [viewer])
    assert (
        env.bob.post(
            "/api/v1/settings/system_prompt/test",
            json={"question": "q", "system_prompt": "x"},
        ).status_code
        == 403
    )


def test_ai_log_detail_permission_and_list_error_redaction(env):
    import json
    viewer = role(env.admin, ["ai_logs.view"])
    assign(env, env.alice_id, [viewer])
    private_error = json.dumps({"call_id": "305497e8711044d1", "http_status": 502,
                                "response_error": "PRIVATE_RESPONSE", "phase": "PRIVATE_PHASE",
                                "exception_chain": [{"message": "PRIVATE_EXCEPTION"}]})
    with actor(env.alice_id):
        lid = db.insert_ai_call_log(provider="p", model="m", scene="qa_chat",
                                    messages=[{"role": "user", "content": "PRIVATE_INPUT"}],
                                    error_message=private_error, success=False)
    result = env.alice.get("/api/v1/ai_logs")
    assert result.status_code == 200
    assert "PRIVATE_" not in result.text
    assert "HTTP 502" in result.text
    assert "305497e8711044d1" in result.text
    assert env.alice.get(f"/api/v1/ai_logs/{lid}").status_code == 403
    assert env.alice.get("/api/v1/ai_logs/stats").status_code == 200
    detailed = role(env.admin, ["ai_logs.view", "ai_logs.detail"])
    assign(env, env.alice_id, [detailed])
    assert "PRIVATE_INPUT" in env.alice.get(f"/api/v1/ai_logs/{lid}").text
    assert env.bob.get(f"/api/v1/ai_logs/{lid}").status_code == 404
    assert env.admin.get(f"/api/v1/ai_logs/{lid}").status_code == 404
    assign(env, env.alice_id, [viewer])
    assert env.alice.get(f"/api/v1/ai_logs/{lid}").status_code == 403
    assert env.admin.post("/api/v1/admin/roles", json={"name": "invalid-detail", "permissions": ["ai_logs.detail"]}).status_code == 400


def test_ai_log_detail_migration_only_once(env):
    import json
    from src.core import permissions as p
    rid = role(env.admin, ["ai_logs.view"])
    empty = role(env.admin, [])
    a.execute("DELETE FROM permission_seed WHERE id=3")
    p.init()
    caps = json.loads(a.rows("SELECT permissions FROM permission_roles WHERE id=?", (rid,))[0]["permissions"])
    assert "ai_logs.detail" in caps
    assert json.loads(a.rows("SELECT permissions FROM permission_roles WHERE id=?", (empty,))[0]["permissions"]) == []
    a.execute("UPDATE permission_roles SET permissions=? WHERE id=?", (json.dumps(["ai_logs.view"]), rid))
    p.init()
    assert json.loads(a.rows("SELECT permissions FROM permission_roles WHERE id=?", (rid,))[0]["permissions"]) == ["ai_logs.view"]


def test_download_inherited_from_role_and_revoked_at_source(env):
    kid = kb(env.alice)
    shared = role(env.admin, [])
    assign(env, env.bob_id, ["member", shared])
    grant(env.alice, "kb", kid, env.bob_id, ["query"])
    grant(env.alice, "kb", kid, shared, ["query", "download"], "role")
    base = a.settings.feed_folder / kid
    base.mkdir(parents=True, exist_ok=True)
    source = base / "role-download.txt"
    source.write_text("role content")
    db.upsert_file(relative_path=source.name, absolute_path=str(source), content_hash="role-hash",
                   file_size=12, file_type="txt", kb_id=kid)
    fid = db.get_file_by_path(source.name, kb_id=kid)["id"]
    path = f"/api/v1/knowledge/download/{fid}?kb_id={kid}"
    assert env.bob.get(path).text == "role content"
    effective = env.alice.get(f"/api/v1/access/kb/{kid}/effective/{env.bob_id}").json()
    assert any(s["source"].startswith("角色：") and "download" in s["actions"] for s in effective["sources"])
    grant(env.alice, "kb", kid, shared, ["query"], "role")
    assert env.bob.get(path).status_code == 403
    assert env.bob.get(f"/api/v1/kbs/{kid}").status_code == 200


@pytest.mark.parametrize("message", ["PRIVATE_TEXT", '["PRIVATE_TEXT"]', '{"http_status": "PRIVATE_TEXT", "call_id": "PRIVATE_TEXT"}'])
def test_list_error_summary_never_forwards_unstructured_content(message):
    from src.api.routes_ai_logs import safe_error_summary
    assert safe_error_summary(message) == "调用失败"
