"""Multi-model selection boundaries; all requests use an isolated test database."""

# ruff: noqa: F811 -- imported pytest fixture is injected by name
import pytest
from fastapi import HTTPException

from tests.test_accounts_access import env, actor  # noqa: F401
from src.core import accounts as a, api_models


def create(client, scope="personal", model_ids=("model-a", "model-b")):
    response = client.post(
        "/api/v1/providers",
        json=dict(
            name="Test API",
            scope=scope,
            base_url="https://example.invalid/v1",
            protocol="openai",
            api_key="test-secret",
            models=[dict(model_id=m, display_name=m.upper()) for m in model_ids],
        ),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def choose(client, pid, model="model-b"):
    response = client.put(
        "/api/v1/account/provider", json=dict(provider_id=pid, model_id=model)
    )
    assert response.status_code == 200, response.text
    return response.json()


def session(client, sid):
    response = client.post("/api/v1/sessions", json=dict(id=sid, created_at=1))
    assert response.status_code == 201, response.text


def update(client, pid, **changes):
    p = next(
        p for p in client.get("/api/v1/providers").json()["items"] if p["id"] == pid
    )
    return client.put(
        f"/api/v1/providers/{pid}", json={**p, "api_key": None, **changes}
    )


def test_catalog_and_default_model_no_fallback(env):
    pid = create(env.alice)
    assert choose(env.alice, pid)["model_id"] == "model-b"
    with actor(env.alice_id):
        assert a.resolve_provider()["chat_model"] == "model-b"
        # Explicit legacy ID selects only the defined compatibility model.
        assert a.resolve_provider(pid)["chat_model"] == "model-a"
        with pytest.raises(HTTPException):
            a.resolve_provider(pid, "not-configured")
    assert update(env.alice, pid, models=[dict(model_id="model-a")]).status_code == 200
    state = env.alice.get("/api/v1/account/api-preferences").json()
    assert not state["valid"] and state["model_id"] == "model-b"
    with actor(env.alice_id), pytest.raises(HTTPException):
        a.resolve_provider()
    assert (
        env.bob.put(
            "/api/v1/account/provider", json=dict(provider_id=pid, model_id="model-a")
        ).status_code
        == 403
    )


def test_sessions_are_independent_and_do_not_follow_new_default(env):
    pid = create(env.alice)
    choose(env.alice, pid, "model-a")
    session(env.alice, "session-one")
    session(env.alice, "session-two")
    path = "/api/v1/sessions/session-one/model-selection"
    assert (
        env.alice.put(path, json=dict(provider_id=pid, model_id="model-b")).status_code
        == 200
    )
    assert env.alice.get(path).json()["model_id"] == "model-b"
    assert (
        env.alice.get("/api/v1/sessions/session-two/model-selection").json()["model_id"]
        == "model-a"
    )
    assert (
        env.alice.get("/api/v1/account/api-preferences").json()["model_id"] == "model-a"
    )
    choose(env.alice, pid, "model-b")
    assert (
        env.alice.get("/api/v1/sessions/session-two/model-selection").json()["model_id"]
        == "model-a"
    )
    assert env.bob.get(path).status_code in (403, 404)
    assert env.bob.put(
        path, json=dict(provider_id=pid, model_id="model-a")
    ).status_code in (403, 404)
    assert update(env.alice, pid, models=[dict(model_id="model-a")]).status_code == 200
    assert not env.alice.get(path).json()["valid"]


def test_team_model_selection_requires_grant_and_stops_after_revocation(env):
    pid = create(env.admin, "team")
    assert (
        env.admin.put(
            "/api/v1/account/provider", json=dict(provider_id=pid, model_id="model-b")
        ).status_code
        == 403
    )
    assert (
        env.admin.post(f"/api/v1/providers/{pid}/test?model_id=model-b").status_code
        == 403
    )
    assert (
        env.admin.put(f"/api/v1/providers/{pid}/members/{env.alice_id}").status_code
        == 200
    )
    choose(env.alice, pid)
    session(env.alice, "team-session")
    assert env.alice.get("/api/v1/sessions/team-session/model-selection").json()[
        "valid"
    ]
    assert (
        env.admin.delete(f"/api/v1/providers/{pid}/members/{env.alice_id}").status_code
        == 200
    )
    state = env.alice.get("/api/v1/sessions/team-session/model-selection").json()
    assert not state["valid"] and "provider_name" not in state


def test_model_snapshot_and_revocation(env, monkeypatch):
    from src.core.account_llm import chat_client
    from src.core.llm_providers import OpenAIProvider
    from src.core import ai_call_logger

    calls = []
    monkeypatch.setattr(ai_call_logger, "log_call", lambda **kwargs: None)
    monkeypatch.setattr(
        OpenAIProvider,
        "chat",
        lambda self, *args, **kwargs: calls.append((self.chat_model, self.base_url))
        or "OK",
    )
    pid = create(env.alice)
    with actor(env.alice_id):
        a.bind_provider(pid, model_id="model-b")
        a.execute(
            "UPDATE auth_providers SET base_url='https://changed.invalid/v1' WHERE id=?",
            (pid,),
        )
        client = chat_client()
        assert client.chat([])[0] == "OK"
        assert calls == [("model-b", "https://example.invalid/v1")]
        a.execute(
            "DELETE FROM api_models WHERE provider_id=? AND model_id='model-b'", (pid,)
        )
        with pytest.raises(HTTPException):
            client.chat([])
    assert len(calls) == 1


def test_legacy_migration_idempotent_and_disabled_preserved(env):
    pid = create(env.alice, model_ids=("legacy-model",))
    choose(env.alice, pid, "legacy-model")
    a.execute("DELETE FROM api_models WHERE provider_id=?", (pid,))
    a.execute("DELETE FROM api_preferences WHERE user_id=?", (env.alice_id,))
    a.execute("UPDATE auth_providers SET enabled=0 WHERE id=?", (pid,))
    api_models.init_schema()
    api_models.init_schema()
    assert len(a.rows("SELECT * FROM api_models WHERE provider_id=?", (pid,))) == 1
    with actor(env.alice_id):
        state = api_models.selection_status(api_models.preferences())
        assert state["model_id"] == "legacy-model" and not state["valid"]
    assert update(env.alice, pid, enabled=True).status_code == 200
    assert (
        update(env.alice, pid, models=[dict(model_id="new-model")]).status_code == 200
    )
    api_models.init_schema()
    assert (
        env.alice.get("/api/v1/account/api-preferences").json()["model_id"]
        == "legacy-model"
    )


def test_catalog_validation_and_legacy_update(env):
    response = env.alice.post(
        "/api/v1/providers",
        json=dict(
            name="bad",
            base_url="https://example.invalid",
            models=[dict(model_id=" a "), dict(model_id="a")],
        ),
    )
    assert response.status_code == 422
    pid = create(env.alice)
    provider = next(
        p for p in env.alice.get("/api/v1/providers").json()["items"] if p["id"] == pid
    )
    provider.pop("models")
    provider["api_key"] = None
    assert env.alice.put(f"/api/v1/providers/{pid}", json=provider).status_code == 200
    provider["chat_model"] = "changed"
    assert env.alice.put(f"/api/v1/providers/{pid}", json=provider).status_code == 409
    assert (
        len(
            next(
                p
                for p in env.alice.get("/api/v1/providers").json()["items"]
                if p["id"] == pid
            )["models"]
        )
        == 2
    )


def test_detection_is_per_model_and_invalidates_on_edit(env, monkeypatch):
    from src.core.llm_providers import OpenAIProvider

    seen = []
    monkeypatch.setattr(
        OpenAIProvider,
        "test_connection",
        lambda self: seen.append(self.chat_model) or "OK",
    )
    pid = create(env.alice)
    assert (
        env.alice.post(f"/api/v1/providers/{pid}/test?model_id=model-b").status_code
        == 200
    )
    assert seen == ["model-b"]
    catalog = a.rows(
        "SELECT * FROM api_models WHERE provider_id=? ORDER BY model_id", (pid,)
    )
    assert (
        catalog[0]["connection_status"] is None
        and catalog[1]["connection_status"] == "passed"
    )
    with actor(env.alice_id):
        frozen = a.resolve_provider(pid, "model-b")
    assert update(env.alice, pid, base_url="https://new.invalid/v1").status_code == 200
    api_models.record_test(frozen, "connection", "passed", "stale")
    assert all(
        m["connection_status"] is None
        for m in a.rows("SELECT * FROM api_models WHERE provider_id=?", (pid,))
    )


def test_retry_preference_saved_per_user_and_invalid_selection_retained(env):
    pid = create(env.alice)
    choose(env.alice, pid)
    assert env.alice.delete(f"/api/v1/providers/{pid}").status_code == 200
    result = env.alice.put("/api/v1/account/api-preferences", json=dict(retry_count=2))
    assert result.status_code == 200
    assert result.json()["retry_count"] == 2 and not result.json()["valid"]
    assert env.alice.get("/api/v1/account/api-preferences").json()["retry_count"] == 2
    assert env.bob.get("/api/v1/account/api-preferences").json()["retry_count"] == 10
    assert (
        env.alice.put(
            "/api/v1/account/api-preferences", json=dict(retry_count=11)
        ).status_code
        == 422
    )


def test_request_and_title_use_explicit_model_without_mutating_default(
    env, monkeypatch
):
    from tests.test_accounts_access import kb, session as create_session
    from src.api import routes_qa
    from src.core import llm_client

    pid = create(env.alice)
    choose(env.alice, pid, "model-a")
    kid = kb(env.alice)
    sid = create_session(env.alice, kid)
    seen = []

    async def fake(**kwargs):
        seen.append(a.provider_snapshot.get()["chat_model"])
        yield {
            "type": "done",
            "data": {"answer": "OK", "trace": [], "sources": [], "used_chunks": 0},
        }

    monkeypatch.setattr(routes_qa, "ask_stream", fake)
    path = f"/api/v1/sessions/{sid}/model-selection"
    assert (
        env.alice.put(path, json=dict(provider_id=pid, model_id="model-b")).status_code
        == 200
    )
    body = dict(question="q", session_id=sid, kb_scope=kid, mode="deep_ai")
    response = env.alice.post("/api/v1/qa/ask_stream", json=body)
    assert response.status_code == 200, response.text
    assert seen == ["model-b"]
    response = env.alice.post(
        "/api/v1/qa/ask_stream",
        json={**body, "provider_id": pid, "model_id": "unknown"},
    )
    assert response.status_code == 409
    assert seen == ["model-b"]
    from types import SimpleNamespace

    monkeypatch.setattr(
        llm_client,
        "get_client",
        lambda: SimpleNamespace(
            chat=lambda *args, **kwargs: (
                seen.append(a.provider_snapshot.get()["chat_model"]) or "标题",
                "test",
            )
        ),
    )
    assert (
        env.alice.post(
            "/api/v1/qa/summarize-title",
            json=dict(
                question="q",
                answer="a",
                session_id=sid,
                provider_id=pid,
                model_id="model-b",
            ),
        ).status_code
        == 200
    )
    assert seen == ["model-b", "model-b"]
    assert (
        env.alice.get("/api/v1/account/api-preferences").json()["model_id"] == "model-a"
    )


def test_call_failure_never_tries_other_models(env, monkeypatch):
    from src.core.account_llm import chat_client
    from src.core.llm_providers import OpenAIProvider, LLMError
    from src.core import ai_call_logger

    calls = []
    monkeypatch.setattr(ai_call_logger, "log_call", lambda **kwargs: None)

    def fail(provider, *args, **kwargs):
        calls.append(provider.chat_model)
        raise LLMError("rejected")

    monkeypatch.setattr(OpenAIProvider, "chat", fail)
    pid = create(env.alice)
    with actor(env.alice_id):
        a.bind_provider(pid, model_id="model-b")
        with pytest.raises(Exception, match="rejected"):
            chat_client().chat([], api_retry_count=0)
    assert calls == ["model-b"]


def test_deleted_compatibility_model_never_selects_another(env):
    pid = create(env.alice)
    assert update(env.alice, pid, models=[dict(model_id="model-b")]).status_code == 200
    with actor(env.alice_id):
        with pytest.raises(HTTPException, match="所选模型已删除"):
            a.resolve_provider(pid)
        assert a.resolve_provider(pid, "model-b")["chat_model"] == "model-b"
    assert choose(env.alice, pid, "model-b")["valid"]


def test_model_capacity_preserved_for_legacy_updates(env):
    pid = create(env.alice)
    result = update(env.alice, pid, models=[dict(model_id='model-a', context_tokens=64000, output_tokens=16000)])
    assert result.status_code == 200, result.text
    assert update(env.alice, pid, models=[dict(model_id='model-a', display_name='new')]).status_code == 200
    with actor(env.alice_id):
        model = a.resolve_provider(pid, 'model-a')
        assert model['context_tokens'] == 64000
        assert model['output_tokens'] == 16000
    assert update(env.alice, pid, models=[dict(model_id='model-a', context_tokens=4096, output_tokens=16000)]).status_code == 422
