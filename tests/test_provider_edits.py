"""Explicit identity edits preserve live selections, never historical request snapshots."""

# ruff: noqa: F811
import pytest
from fastapi import HTTPException
from tests.test_accounts_access import env, actor  # noqa: F401
from tests.test_api_models import create, choose, session, update
from src.core import accounts as a, api_models


def body(client, pid, models=None, **changes):
    p = next(
        p for p in client.get("/api/v1/providers").json()["items"] if p["id"] == pid
    )
    return {
        **p,
        "api_key": None,
        "models": models
        or [{**m, "original_model_id": m["model_id"]} for m in p["models"]],
        **changes,
    }


def preview(client, pid, data):
    return client.post(f"/api/v1/providers/{pid}/edit-preview", json=data)


def save(client, pid, data):
    planned = preview(client, pid, data)
    assert planned.status_code == 200, planned.text
    return client.put(
        f"/api/v1/providers/{pid}",
        json={**data, "confirmation_token": planned.json()["confirmation_token"]},
    )


def model(old, new, **values):
    return dict(original_model_id=old, model_id=new, **values)


def test_rename_moves_default_sessions_compatibility_and_limits(env):
    pid = create(env.alice, model_ids=("old",))
    assert (
        update(
            env.alice,
            pid,
            models=[dict(model_id="old", context_tokens=64000, output_tokens=16000)],
        ).status_code
        == 200
    )
    choose(env.alice, pid, "old")
    session(env.alice, "rename-session")
    with actor(env.alice_id):
        frozen = a.resolve_provider(pid, "old")
    api_models.record_test(frozen, "connection", "passed", "old-result")
    data = body(env.alice, pid, [model("old", "new", display_name="friendly")])
    plan = preview(env.alice, pid, data).json()
    assert plan["renames"] == [
        dict(old_model_id="old", model_id="new", defaults=1, sessions=1)
    ]
    # Preview must not write catalog, selections or audit records.
    assert env.alice.get("/api/v1/account/api-preferences").json()["model_id"] == "old"
    assert save(env.alice, pid, data).status_code == 200
    assert env.alice.get("/api/v1/account/api-preferences").json()["model_id"] == "new"
    selection = env.alice.get("/api/v1/sessions/rename-session/model-selection").json()
    assert selection["model_id"] == "new" and selection["valid"]
    with actor(env.alice_id):
        new = a.resolve_provider(pid)
        assert new["chat_model"] == "new" and new["model_label"] == "friendly"
        assert (new["context_tokens"], new["output_tokens"]) == (64000, 16000)
        with pytest.raises(HTTPException):
            a.resolve_provider(pid, frozen["chat_model"])
    api_models.record_test(frozen, "connection", "passed", "late-old-result")
    assert (
        a.rows("SELECT connection_status FROM api_models WHERE provider_id=?", (pid,))[
            0
        ]["connection_status"]
        is None
    )


def test_display_only_keeps_selection_detection_and_revision(env):
    pid = create(env.alice, model_ids=("old",))
    choose(env.alice, pid, "old")
    session(env.alice, "display-session")
    with actor(env.alice_id):
        frozen = a.resolve_provider(pid, "old")
    api_models.record_test(frozen, "connection", "passed", "OK")
    data = body(env.alice, pid, [model("old", "old", display_name="New label")])
    p = preview(env.alice, pid, data).json()
    assert p["renames"] == p["deletions"] == []
    assert save(env.alice, pid, data).status_code == 200
    row = a.rows("SELECT * FROM api_models WHERE provider_id=?", (pid,))[0]
    assert (
        row["revision"] == frozen["model_revision"]
        and row["connection_status"] == "passed"
    )
    assert (
        env.alice.get("/api/v1/sessions/display-session/model-selection").json()[
            "model_label"
        ]
        == "New label"
    )


def test_team_impact_all_users_and_permission_boundaries(env):
    pid = create(env.admin, scope="team", model_ids=("old",))
    for client, uid, sid in [
        (env.alice, env.alice_id, "alice-team"),
        (env.bob, env.bob_id, "bob-team"),
    ]:
        assert (
            env.admin.put(f"/api/v1/providers/{pid}/members/{uid}").status_code == 200
        )
        choose(client, pid, "old")
        session(client, sid)
    data = body(env.admin, pid, [model("old", "new")])
    assert preview(env.alice, pid, data).status_code == 403
    assert env.alice.put(f"/api/v1/providers/{pid}", json=data).status_code == 403
    p = preview(env.admin, pid, data).json()
    assert p["renames"][0]["defaults"] == p["renames"][0]["sessions"] == 2
    assert save(env.admin, pid, data).status_code == 200
    for client in [env.alice, env.bob]:
        assert client.get("/api/v1/account/api-preferences").json()["model_id"] == "new"
    assert (
        env.admin.delete(f"/api/v1/providers/{pid}/members/{env.alice_id}").status_code
        == 200
    )
    assert not env.alice.get("/api/v1/sessions/alice-team/model-selection").json()[
        "valid"
    ]


def test_delete_add_not_inferred_as_rename_and_legacy_remains_explicit(env):
    pid = create(env.alice, model_ids=("old",))
    choose(env.alice, pid, "old")
    session(env.alice, "deleted-session")
    data = body(env.alice, pid, [dict(model_id="new")])
    p = preview(env.alice, pid, data).json()
    assert p["renames"] == [] and p["deletions"][0]["sessions"] == 1
    assert env.alice.put(f"/api/v1/providers/{pid}", json=data).status_code == 409
    assert save(env.alice, pid, data).status_code == 200
    assert not env.alice.get("/api/v1/sessions/deleted-session/model-selection").json()[
        "valid"
    ]
    with actor(env.alice_id), pytest.raises(HTTPException):
        a.resolve_provider(pid)


@pytest.mark.parametrize(
    "models",
    [
        [model("model-a", "model-b")],
        [model("model-a", "new-a"), model("model-a", "new-b")],
        [model("missing", "new")],
        [model("model-a", "model-b"), model("model-b", "model-a")],
        [model("model-a", "new"), dict(model_id="model-a")],
    ],
)
def test_conflicting_identities_rejected_without_writes(env, models):
    pid = create(env.alice)
    data = body(env.alice, pid, models)
    assert preview(env.alice, pid, data).status_code == 409
    assert env.alice.put(f"/api/v1/providers/{pid}", json=data).status_code == 409
    assert {
        r["model_id"]
        for r in a.rows("SELECT model_id FROM api_models WHERE provider_id=?", (pid,))
    } == {"model-a", "model-b"}


def test_stale_edit_and_changed_impact_are_rejected(env):
    pid = create(env.alice, model_ids=("old",))
    choose(env.alice, pid, "old")
    data = body(env.alice, pid, [model("old", "new")])
    p = preview(env.alice, pid, data).json()
    session(env.alice, "created-after-preview")
    assert (
        env.alice.put(
            f"/api/v1/providers/{pid}",
            json={**data, "confirmation_token": p["confirmation_token"]},
        ).status_code
        == 409
    )
    assert update(env.alice, pid, name="concurrent edit").status_code == 200
    assert preview(env.alice, pid, data).status_code == 409
    assert env.alice.get("/api/v1/account/api-preferences").json()["model_id"] == "old"


def test_transaction_rolls_back_late_capacity_error(env):
    pid = create(env.alice)
    choose(env.alice, pid, "model-a")
    session(env.alice, "rollback-session")
    data = body(
        env.alice,
        pid,
        [
            model("model-a", "new-a"),
            model("model-b", "new-b", context_tokens=4096, output_tokens=8192),
        ],
    )
    assert save(env.alice, pid, data).status_code == 422
    with actor(env.alice_id):
        assert a.resolve_provider(pid)["chat_model"] == "model-a"
    assert (
        env.alice.get("/api/v1/sessions/rollback-session/model-selection").json()[
            "model_id"
        ]
        == "model-a"
    )
    assert {
        r["model_id"]
        for r in a.rows("SELECT model_id FROM api_models WHERE provider_id=?", (pid,))
    } == {"model-a", "model-b"}


def test_multiple_renames_implicit_selection_and_provider_isolation(env):
    pid = create(env.alice)
    other = create(env.alice)
    choose(env.alice, pid, "model-a")
    session(env.alice, "implicit-session")
    a.execute(
        "UPDATE api_preferences SET model_id=NULL WHERE user_id=?", (env.alice_id,)
    )
    a.execute(
        "UPDATE api_session_selection SET model_id=NULL WHERE session_id='implicit-session'"
    )
    data = body(env.alice, pid, [model("model-a", "new-a"), model("model-b", "new-b")])
    assert save(env.alice, pid, data).status_code == 200
    assert (
        env.alice.get("/api/v1/account/api-preferences").json()["model_id"] == "new-a"
    )
    assert (
        env.alice.get("/api/v1/sessions/implicit-session/model-selection").json()[
            "model_id"
        ]
        == "new-a"
    )
    assert {
        r["model_id"]
        for r in a.rows("SELECT model_id FROM api_models WHERE provider_id=?", (other,))
    } == {"model-a", "model-b"}


def test_private_preview_forbidden_and_stale_selection_cannot_overwrite_migration(env):
    pid = create(env.alice, model_ids=("old",))
    choose(env.alice, pid, "old")
    session(env.alice, "stale-selection")
    data = body(env.alice, pid, [model("old", "new")])
    assert preview(env.bob, pid, data).status_code in (403, 404)
    assert env.bob.put(f"/api/v1/providers/{pid}", json=data).status_code in (403, 404)
    assert save(env.alice, pid, data).status_code == 200
    assert (
        env.alice.put(
            "/api/v1/account/provider", json=dict(provider_id=pid, model_id="old")
        ).status_code
        == 409
    )
    assert (
        env.alice.put(
            "/api/v1/sessions/stale-selection/model-selection",
            json=dict(provider_id=pid, model_id="old"),
        ).status_code
        == 409
    )
    assert env.alice.get("/api/v1/account/api-preferences").json()["model_id"] == "new"
    assert (
        env.alice.get("/api/v1/sessions/stale-selection/model-selection").json()[
            "model_id"
        ]
        == "new"
    )


def test_history_unchanged_and_inflight_old_model_rejected(env):
    from src.core.account_llm import chat_client

    pid = create(env.alice, model_ids=("old",))
    choose(env.alice, pid, "old")
    session(env.alice, "history-session")
    a.execute("""INSERT INTO turns(id,session_id,order_idx,question,answer,used_provider,created_at)
        VALUES ('historical-turn','history-session',0,'question','answer','old',1)""")
    before = a.rows("SELECT * FROM turns WHERE id='historical-turn'")
    with actor(env.alice_id):
        a.bind_provider(pid, model_id="old")
        client = chat_client()
    try:
        assert (
            save(
                env.alice, pid, body(env.alice, pid, [model("old", "new")])
            ).status_code
            == 200
        )
        with actor(env.alice_id), pytest.raises(HTTPException):
            client.check_access()
        assert a.rows("SELECT * FROM turns WHERE id='historical-turn'") == before
    finally:
        client.close()


def test_rename_back_does_not_accept_old_test_result(env):
    pid = create(env.alice, model_ids=("old",))
    with actor(env.alice_id):
        frozen = a.resolve_provider(pid, "old")
    assert (
        save(env.alice, pid, body(env.alice, pid, [model("old", "new")])).status_code
        == 200
    )
    assert (
        save(env.alice, pid, body(env.alice, pid, [model("new", "old")])).status_code
        == 200
    )
    api_models.record_test(frozen, "connection", "passed", "obsolete")
    row = a.rows("SELECT * FROM api_models WHERE provider_id=?", (pid,))[0]
    assert (
        row["connection_status"] is None and row["revision"] != frozen["model_revision"]
    )
