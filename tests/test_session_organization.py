"""Authenticated regression tests for personal groups, titles and KB names."""
import uuid
import sqlite3
from tests.test_accounts_access import env, actor, kb, session
from src.db import metadata_db as db
from src.api import routes_sessions


def test_groups_titles_and_cross_account_isolation(env):
    alice, bob = env.alice, env.bob
    kid = kb(alice)
    group = alice.post("/api/v1/sessions/groups", json={"name": "专题"})
    assert group.status_code == 201, group.text
    gid = group.json()["id"]
    assert bob.get("/api/v1/sessions/groups").json() == []
    assert bob.patch(f"/api/v1/sessions/groups/{gid}", json={"name": "偷改"}).status_code == 404
    assert bob.delete(f"/api/v1/sessions/groups/{gid}").status_code == 404
    assert alice.post("/api/v1/sessions/groups", json={"name": " 专题 "}).status_code == 409
    other = alice.post("/api/v1/sessions/groups", json={"name": "其它"}).json()["id"]
    assert alice.patch(f"/api/v1/sessions/groups/{other}", json={"direction": "up"}).status_code == 200
    assert alice.get("/api/v1/sessions/groups").json()[0]["id"] == other
    sid = uuid.uuid4().hex
    response = alice.post("/api/v1/sessions", json={"id": sid, "title": "新会话", "created_at": 1000, "kb_scope": kid, "group_id": gid})
    assert response.status_code == 201, response.text
    assert response.json()["group_id"] == gid
    bob_sid = session(bob, kb(bob))
    assert bob.patch(f"/api/v1/sessions/{bob_sid}", json={"group_id": gid}).status_code == 404
    url = f"/api/v1/sessions/{sid}"
    assert alice.patch(url, json={"title": "  我的会话  "}).json()["title"] == "我的会话"
    assert alice.patch(url, json={"title": "迟到的AI名称", "automatic_title": True}).json()["title"] == "我的会话"
    assert alice.patch(url, json={"title": " "}).status_code == 422
    assert bob.patch(url, json={"title": "别人的名称"}).status_code == 404
    assert alice.post(url + "/turns", json={"id": sid + "_t", "question": "q", "created_at": 1234, "mode": "basic"}).status_code == 201
    assert alice.get(url).json()["turns"][0]["created_at"] == 1234
    # Export/import preserve the personal group by name, never a foreign group ID.
    with actor(env.alice_id):
        payload = routes_sessions._build_session_payload(sid)
        assert payload["session"]["group_name"] == "专题"
        result = routes_sessions._import_one_session(payload)
        assert db.get_session(result["id"])["group_id"] == gid
    assert alice.delete(f"/api/v1/sessions/groups/{gid}").status_code == 200
    restored = alice.get(url).json()
    assert restored["group_id"] is None and len(restored["turns"]) == 1
    assert restored["title"] == "我的会话"


def test_kb_scoped_names_and_owner_labels(env, monkeypatch):
    from src.core import vector_store
    monkeypatch.setattr(vector_store, "reset_collection", lambda *args, **kwargs: None)
    a = kb(env.alice, "行情")
    b = kb(env.bob, "行情")
    assert a != b
    assert env.alice.post("/api/v1/kbs", json={"name": " 行情 "}).status_code == 409
    other = kb(env.alice, "其他")
    assert env.alice.put(f"/api/v1/kbs/{other}", json={"name": "行情"}).status_code == 409
    visible = env.alice.get("/api/v1/kbs").json()
    assert next(k for k in visible if k["id"] == a)["owner_username"] == "alice"
    assert b not in [k["id"] for k in visible]
    assert env.admin.post("/api/v1/kbs", json={"name": "团队行情", "scope": "team"}).status_code == 201
    assert env.admin.post("/api/v1/kbs", json={"name": "团队行情", "scope": "team"}).status_code == 409
    # Existing duplicate names remain readable and gain a display disambiguator.
    with db.get_cursor() as cur:
        cur.execute("UPDATE kbs SET name='行情' WHERE id=?", (other,))
    duplicates = [k for k in env.alice.get("/api/v1/kbs").json() if k["name"] == "行情"]
    assert len(duplicates) == 2 and all(k["name_conflict"] for k in duplicates)
    assert env.alice.put(f"/api/v1/kbs/{other}", json={"name": "行情", "description": "备注"}).status_code == 200


def test_v18_migration_preserves_sessions():
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY,title TEXT)")
    cur.execute("INSERT INTO sessions VALUES ('old','旧会话')")
    db._migrate_v18_to_v19(cur)
    db._migrate_v18_to_v19(cur)
    assert cur.execute("SELECT title,group_id,title_source FROM sessions WHERE id='old'").fetchone() == ('旧会话', None, 'manual')
    conn.close()
