"""Cross-account and revocation regression tests, with no external model calls."""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

from src.core import accounts as a, llm_client, vector_store
from src.db import metadata_db as db


@contextmanager
def actor(uid):
    token = a.identity.set(a.rows("SELECT * FROM auth_users WHERE id=?", (uid,))[0])
    try:
        yield
    finally:
        a.identity.reset(token)


@pytest.fixture
def env(monkeypatch):
    db.init_db()
    previous = a.enabled
    a.init_auth()
    for table in [
        "auth_provider_grants",
        "auth_kb_grants",
        "auth_sessions",
        "auth_objects",
        "auth_providers",
        "auth_users",
        "auth_limits",
        "auth_audit",
        "turns",
        "sessions",
    ]:
        a.execute(f"DELETE FROM {table}")
    token_path = a.USER_DATA_DIR / "setup-token.txt"
    token_path.write_text("local-setup-token")
    a.bootstrap("admin", "initial-admin-password", "local-setup-token")
    from src.api.main import app

    admin = TestClient(app)
    admin.headers["X-Insight-Request"] = "1"
    response = admin.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "initial-admin-password"},
    )
    assert response.status_code == 200, response.text
    admin.headers["X-CSRF-Token"] = response.json()["csrf"]
    admin_id = response.json()["id"]

    def member(name):
        response = admin.post(
            "/api/v1/admin/users",
            json={"username": name, "password": "initial-member-password"},
        )
        assert response.status_code == 201, response.text
        uid = response.json()["id"]
        client = TestClient(app)
        client.headers["X-Insight-Request"] = "1"
        login = client.post(
            "/api/v1/auth/login",
            json={"username": name, "password": "initial-member-password"},
        )
        client.headers["X-CSRF-Token"] = login.json()["csrf"]
        assert client.get("/api/v1/sessions").status_code == 403
        assert (
            client.post(
                "/api/v1/auth/password",
                json={
                    "old_password": "initial-member-password",
                    "new_password": "my-own-password-123",
                },
            ).status_code
            == 200
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"username": name, "password": "my-own-password-123"},
        )
        client.headers["X-CSRF-Token"] = login.json()["csrf"]
        return client, uid

    alice, alice_id = member("alice")
    bob, bob_id = member("bob")
    monkeypatch.setattr(
        vector_store, "get_or_create_collection", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(vector_store, "get_collection_dim", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        llm_client,
        "get_client",
        lambda: SimpleNamespace(embed=lambda texts: ([[0.1, 0.2]], "local")),
    )
    yield SimpleNamespace(
        app=app,
        admin=admin,
        admin_id=admin_id,
        alice=alice,
        alice_id=alice_id,
        bob=bob,
        bob_id=bob_id,
    )
    a.identity.set(None)
    a.selected_provider.set(None)
    a.active_kb.set(None)
    a.provider_snapshot.set(None)
    a.enabled = previous
    admin.close()
    alice.close()
    bob.close()


def kb(client, name="Private"):
    r = client.post("/api/v1/kbs", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def session(client, kid):
    sid = uuid.uuid4().hex
    r = client.post(
        "/api/v1/sessions",
        json={
            "id": sid,
            "title": "private conversation",
            "created_at": int(time.time() * 1000),
            "kb_scope": kid,
        },
    )
    assert r.status_code == 201, r.text
    return sid


def provider(client, scope="personal"):
    r = client.post(
        "/api/v1/providers",
        json={
            "scope": scope,
            "name": "Private key",
            "protocol": "openai",
            "base_url": "http://127.0.0.1:9999/v1",
            "chat_model": "model",
            "api_key": "secret-test-never-return",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_closed_registration_csrf_and_no_public_data(env):
    anon = TestClient(env.app)
    for path in [
        "/api/v1/sessions",
        "/api/v1/kbs",
        "/api/v1/settings/llm_providers",
        "/api/v1/knowledge/stats",
        "/api/v1/logs/download",
        "/api/info",
        "/api/v1/embedding/status",
    ]:
        assert anon.get(path).status_code == 401, path
    assert anon.post("/api/v1/auth/register", json={}).status_code in (401, 404)
    assert (
        anon.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "initial-admin-password"},
        ).status_code
        == 403
    )
    assert (
        env.admin.post(
            "/api/v1/admin/users",
            headers={"X-CSRF-Token": "wrong"},
            json={"username": "evil", "password": "password-123456"},
        ).status_code
        == 403
    )
    assert anon.get("/health").json() == {"status": "ok"}
    assert env.admin.post(
        "/api/v1/auth/setup",
        json={
            "username": "other",
            "password": "another-password",
            "token": "local-setup-token",
        },
    ).status_code in (403, 409)


def test_private_kbs_chats_exports_and_admin_privacy(env):
    kid = kb(env.alice)
    sid = session(env.alice, kid)
    assert [s["id"] for s in env.alice.get("/api/v1/sessions").json()] == [sid]
    for client in [env.bob, env.admin]:
        assert sid not in [s["id"] for s in client.get("/api/v1/sessions").json()]
        assert kid not in [k["id"] for k in client.get("/api/v1/kbs").json()]
        for path in [f"/sessions/{sid}", f"/sessions/{sid}/export"]:
            assert client.get("/api/v1" + path).status_code == 404
        assert (
            client.post(
                "/api/v1/sessions/export-batch", json={"ids": [sid]}
            ).status_code
            == 404
        )
        assert (
            client.patch(
                f"/api/v1/sessions/{sid}", json={"title": "stolen"}
            ).status_code
            == 404
        )
        assert client.get(f"/api/v1/kbs/{kid}").status_code == 403
    assert env.alice.get(f"/api/v1/kbs/{kid}").json()["role"] == "owner"


def test_kb_grants_read_only_and_revoked_history(env):
    kid = kb(env.alice)
    assert (
        env.alice.put(
            f"/api/v1/kbs/{kid}/members/{env.bob_id}", json={"role": "reader"}
        ).status_code
        == 200
    )
    sid = session(env.bob, kid)
    tid = uuid.uuid4().hex
    r = env.bob.post(
        f"/api/v1/sessions/{sid}/turns",
        json={
            "id": tid,
            "question": "q",
            "answer": "old answer",
            "created_at": 1,
            "sources": [],
            "trace": [],
        },
    )
    assert r.status_code == 201, r.text
    assert (
        env.bob.put(f"/api/v1/kbs/{kid}", json={"name": "overwrite"}).status_code == 403
    )
    assert (
        env.bob.post(
            "/api/v1/knowledge/upload",
            params={"kb_id": kid},
            files={"file": ("x.txt", b"no")},
        ).status_code
        == 403
    )
    assert env.bob.get(f"/api/v1/kbs/{kid}/export").status_code == 403
    assert env.alice.get(f"/api/v1/kbs/{kid}/sessions").json()["count"] == 0
    assert (
        env.alice.delete(f"/api/v1/kbs/{kid}/members/{env.bob_id}").status_code == 200
    )
    assert (
        env.bob.get(f"/api/v1/sessions/{sid}").json()["turns"][0]["answer"]
        == "old answer"
    )
    assert env.bob.get(f"/api/v1/sessions/{sid}/export").status_code == 200
    assert (
        env.bob.post(
            "/api/v1/qa/ask_stream",
            json={
                "question": "new",
                "session_id": sid,
                "kb_scope": kid,
                "mode": "basic",
            },
        ).status_code
        == 403
    )
    assert (
        env.bob.post(
            f"/api/v1/sessions/{sid}/turns",
            json={"id": "another", "question": "q", "created_at": 2},
        ).status_code
        == 403
    )


def test_provider_secrets_ownership_and_team_revocation(env):
    own = provider(env.alice)
    team = provider(env.admin, "team")
    assert "secret-test-never-return" not in env.alice.get("/api/v1/providers").text
    assert (
        "secret-test-never-return"
        not in env.alice.get("/api/v1/settings/llm_providers").text
    )
    assert own not in [
        p["id"] for p in env.admin.get("/api/v1/providers").json()["items"]
    ]
    assert env.admin.delete(f"/api/v1/providers/{own}").status_code == 404
    assert (
        env.alice.put(
            "/api/v1/account/provider", json={"provider_id": team}
        ).status_code
        == 403
    )
    assert (
        env.admin.put(f"/api/v1/providers/{team}/members/{env.alice_id}").status_code
        == 200
    )
    assert (
        env.alice.put(
            "/api/v1/account/provider", json={"provider_id": team}
        ).status_code
        == 200
    )
    assert (
        env.admin.delete(f"/api/v1/providers/{team}/members/{env.alice_id}").status_code
        == 200
    )
    with actor(env.alice_id), pytest.raises(HTTPException):
        a.resolve_provider()  # revoked default never falls back to private/global credentials
    encrypted = a.rows("SELECT secret FROM auth_providers WHERE id=?", (team,))[0][
        "secret"
    ]
    assert "secret-test-never-return" not in encrypted
    assert a.cipher().decrypt(encrypted.encode()).decode() == "secret-test-never-return"


def test_private_ai_logs_stats_and_deletion(env):
    with actor(env.alice_id):
        lid = db.insert_ai_call_log(
            provider="p",
            model="m",
            scene="qa_chat",
            messages=[{"role": "user", "content": "alice private"}],
        )
    with actor(env.bob_id):
        bid = db.insert_ai_call_log(
            provider="p",
            model="m",
            scene="qa_chat",
            messages=[{"role": "user", "content": "bob private"}],
        )
    assert env.alice.get("/api/v1/ai_logs").json()["total"] == 1
    assert env.admin.get(f"/api/v1/ai_logs/{lid}").status_code == 404
    assert env.bob.get(f"/api/v1/ai_logs/{lid}").status_code == 404
    assert env.alice.get("/api/v1/ai_logs/stats").json()["total"] == 1
    assert env.alice.post("/api/v1/ai_logs/delete_all").json()["deleted"] == 1
    assert env.bob.get(f"/api/v1/ai_logs/{bid}").status_code == 200
    assert env.admin.get("/api/v1/logs/tail").status_code == 200
    assert "alice private" not in env.admin.get("/api/v1/logs/tail").text


def test_disabled_reset_and_member_admin_restrictions(env):
    assert env.alice.get("/api/v1/admin/users").status_code == 403
    assert env.alice.get("/api/v1/embedding/status").status_code == 403
    assert (
        env.alice.post("/api/v1/settings/llm_providers/update", json={}).status_code
        == 410
    )
    assert (
        env.admin.patch(
            f"/api/v1/admin/users/{env.admin_id}", json={"disabled": True}
        ).status_code
        == 400
    )
    assert (
        env.admin.patch(
            f"/api/v1/admin/users/{env.alice_id}", json={"disabled": True}
        ).status_code
        == 200
    )
    assert env.alice.get("/api/v1/auth/me").status_code == 401
    assert (
        env.admin.patch(
            f"/api/v1/admin/users/{env.bob_id}", json={"password": "reset-password-123"}
        ).status_code
        == 200
    )
    assert env.bob.get("/api/v1/auth/me").status_code == 401


def test_browser_empty_json_requests_for_grants_and_logout(env):
    team = provider(env.admin, "team")
    headers = {"Content-Type": "application/json"}
    assert (
        env.admin.put(
            f"/api/v1/providers/{team}/members/{env.alice_id}", headers=headers
        ).status_code
        == 200
    )
    assert (
        env.admin.delete(
            f"/api/v1/providers/{team}/members/{env.alice_id}", headers=headers
        ).status_code
        == 200
    )
    assert env.alice.post("/api/v1/auth/logout", headers=headers).status_code == 200
    assert env.alice.get("/api/v1/auth/me").status_code == 401


def test_upload_tasks_and_feedback_never_cross_accounts(env):
    kid = kb(env.alice)
    with actor(env.alice_id):
        tid = uuid.uuid4().hex
        db.create_upload_task(tid, kid, "skip", False, [], upload_complete=False)
        fid = db.enqueue_feedback("private question", "private answer", [], "p", 1)
    assert env.alice.get("/api/v1/knowledge/upload_tasks/active").json()["count"] >= 1
    assert env.bob.get("/api/v1/knowledge/upload_tasks/active").json()["count"] == 0
    assert env.bob.get(f"/api/v1/knowledge/upload_tasks/{tid}").status_code == 404
    assert (
        env.bob.post(
            "/api/v1/knowledge/upload_batch",
            data={"kb_id": kid, "task_id": tid},
            files={"files": ("x.txt", b"attack")},
        ).status_code
        == 404
    )
    assert env.alice.get("/api/v1/feedback/pending").json()["count"] == 1
    assert env.admin.get("/api/v1/feedback/pending").json()["count"] == 0
    assert (
        env.admin.post(
            f"/api/v1/feedback/{fid}/review", json={"decision": "approved"}
        ).status_code
        == 404
    )


def test_provider_snapshot_is_frozen_and_revocation_stops_calls(env, monkeypatch):
    from src.core.account_llm import chat_client
    from src.core.llm_providers import OpenAIProvider
    from src.core import ai_call_logger

    calls = []
    monkeypatch.setattr(ai_call_logger, "log_call", lambda **kwargs: None)
    monkeypatch.setattr(
        OpenAIProvider,
        "chat",
        lambda self, *args, **kwargs: calls.append((self.base_url, self._api_key))
        or "OK",
    )
    pid = provider(env.alice)
    with actor(env.alice_id):
        a.bind_provider(pid)
        a.execute(
            "UPDATE auth_providers SET base_url=? WHERE id=?",
            ("https://changed.example/v1", pid),
        )
        assert chat_client().chat([{"role": "user", "content": "plan"}])[0] == "OK"
        assert chat_client().chat([{"role": "user", "content": "review"}])[0] == "OK"
        assert all(url == "http://127.0.0.1:9999/v1" for url, _ in calls)
        a.execute("UPDATE auth_providers SET enabled=0 WHERE id=?", (pid,))
        with pytest.raises(HTTPException):
            chat_client().chat([{"role": "user", "content": "more"}])
    assert len(calls) == 2


def test_unassigned_background_chat_cannot_use_global_api(env):
    from src.core.account_llm import chat_client

    with pytest.raises(HTTPException):
        chat_client()
    with actor(env.alice_id):
        a.bind_provider(required=False)
        pid = provider(env.alice)
        a.execute(
            "UPDATE auth_users SET default_provider=? WHERE id=?", (pid, env.alice_id)
        )
        with pytest.raises(HTTPException):
            chat_client()  # job started without a provider never picks up a newly added default


def test_importing_same_pack_preserves_private_index_ids(env, monkeypatch, tmp_path):
    import json
    import zipfile
    from src.knowledge import kb_pack
    from src.qa import bm25_index

    recorded = []
    monkeypatch.setattr(
        kb_pack, "settings", SimpleNamespace(feed_folder=tmp_path / "feed")
    )
    monkeypatch.setattr(
        kb_pack,
        "precheck_import",
        lambda path: {
            "compatible": True,
            "target_embedding_model": "test",
            "target_embedding_dim": 2,
        },
    )
    monkeypatch.setattr(
        vector_store, "upsert_chunks", lambda chunks, **kwargs: recorded.extend(chunks)
    )
    monkeypatch.setattr(bm25_index, "add_chunks", lambda chunks: None)
    pack = tmp_path / "pack.zip"
    manifest = {
        "kb": {"name": "Shared pack"},
        "files": [
            {"relative_path": "a.txt", "chunk_ids": ["original"], "file_size": 5}
        ],
    }
    chunk = {
        "id": "original",
        "text": "hello",
        "vector": [0.1, 0.2],
        "metadata": {
            "source_path": "a.txt",
            "file_id": 99999,
            "content_hash": "hash",
            "chunk_index": 0,
        },
    }
    with zipfile.ZipFile(pack, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest))
        z.writestr("documents/a.txt", "hello")
        z.writestr("vectors/chunks.jsonl", json.dumps(chunk) + "\n")
    with actor(env.alice_id):
        left = kb_pack.import_kb_from_zip(pack)
        assert a.owns("kb", left)
    with actor(env.bob_id):
        right = kb_pack.import_kb_from_zip(pack)
        assert a.owns("kb", right)
    assert recorded[0]["id"] != recorded[1]["id"]
    assert recorded[0]["metadata"]["kb_id"] == left
    assert recorded[1]["metadata"]["kb_id"] == right
    assert recorded[0]["metadata"]["file_id"] != recorded[1]["metadata"]["file_id"]
    for item in recorded:
        assert item["id"] == vector_store.make_chunk_id(
            item["metadata"]["source_path"], "hash", 0
        )
