"""One Portal-wide model catalog: filled by a runtime, kept in platform settings, read by every picker and by validation."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.contracts.llm_catalog import COPILOT_MODELS, clear_catalog_models, context_size_options, model_context_window, register_catalog_models
from app.db import Base
from app.models.user import User
from app.repositories.platform_setting_repo import PlatformSettingRepository
from app.services import model_catalog_service as svc

# What a native runtime answers on GET /api/models for a Business seat.
RUNTIME_PAYLOAD = {
    "available": True,
    "source": "catalog",
    "fetched_at": "2026-11-04T01:00:00+00:00",
    "provider": "github-copilot",
    "default_model": "gpt-6-sol",
    "auto": [
        {"id": "auto", "label": "Auto", "tier": "balance"},
        {"id": "auto-efficiency", "label": "Auto (Efficiency)", "tier": "efficiency"},
    ],
    "models": [
        {
            "id": "gpt-6.1-sol", "name": "GPT-6.1 Sol", "selectable": True, "known": True, "vendor": "OpenAI",
            "category": "versatile", "limits": {"context_window_tokens": 1_050_000, "prompt_tokens": 922_000, "output_tokens": 128_000},
            "supports": {"tool_calls": True, "vision": True, "reasoning_efforts": ["low", "medium", "high"]}, "billing_threshold_tokens": 272_000,
        },
        {
            "id": "mai-code-1.1-flash", "name": "MAI-Code-1.1-Flash", "selectable": True, "known": False, "vendor": "Microsoft",
            "limits": {"context_window_tokens": 256_000, "prompt_tokens": 128_000, "output_tokens": 128_000},
        },
        {"id": "gpt-5.3-codex", "name": "GPT-5.3-Codex", "selectable": True, "known": False, "vendor": "OpenAI"},
        {"id": "claude-sonnet-5.5", "name": "Claude Sonnet 5.5", "selectable": False, "reasons": ["not served on /responses"]},
    ],
    "selectable": ["gpt-6.1-sol", "mai-code-1.1-flash", "gpt-5.3-codex"],
    "error": None,
}
EXPECTED_IDS = ("auto", "auto-efficiency", "gpt-6.1-sol", "mai-code-1.1-flash", "gpt-5.3-codex")


@pytest.fixture(autouse=True)
def _clean_state():
    svc.reset_catalog_state()
    yield
    svc.reset_catalog_state()


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def _running_agent():
    return SimpleNamespace(id="agent-1", status="running", service_name="svc", namespace="ns")


def test_project_runtime_models_keeps_auto_and_selectable_rows_in_order():
    projected = svc.project_runtime_models(RUNTIME_PAYLOAD)
    assert projected["source"] == "catalog"
    # Freshness is measured from when the Portal stored the copy; the
    # runtime's own stamp (its hourly cache) rides along for information.
    assert projected["fetched_at"]
    assert projected["fetched_at"] != "2026-11-04T01:00:00+00:00"
    assert projected["runtime_fetched_at"] == "2026-11-04T01:00:00+00:00"
    assert [entry["id"] for entry in projected["models"]] == list(EXPECTED_IDS)
    assert projected["models"][0] == {"id": "auto", "label": "Auto", "kind": "auto", "tier": "balance"}
    sol = projected["models"][2]
    assert sol["label"] == "GPT-6.1 Sol"
    assert sol["kind"] == "model"
    assert sol["vendor"] == "OpenAI"
    assert sol["limits"]["prompt_tokens"] == 922_000
    assert sol["billing_threshold_tokens"] == 272_000
    assert "reasons" not in sol
    # A runtime's static fallback is not a catalog; neither is a catalog with nothing selectable.
    assert svc.project_runtime_models({**RUNTIME_PAYLOAD, "source": "static"}) is None
    assert svc.project_runtime_models({**RUNTIME_PAYLOAD, "models": [RUNTIME_PAYLOAD["models"][3]]}) is None
    assert svc.project_runtime_models({"available": False}) is None
    assert svc.project_runtime_models("nope") is None


def test_the_static_list_stands_until_a_runtime_has_answered(db):
    catalog = svc.current_catalog(db)
    assert catalog.source == "static"
    assert catalog.ids() == tuple(COPILOT_MODELS)
    assert catalog.labels()["auto-efficiency"] == "Auto (Efficiency)"
    assert catalog.is_fresh() is False
    payload = catalog.payload()
    assert payload["source"] == "static"
    assert payload["stale"] is False
    assert payload["model_labels"]["gpt-6-sol"] == "gpt-6-sol"


def test_store_persists_the_catalog_and_a_cold_process_reloads_it(db):
    stored = svc.store_catalog(db, svc.project_runtime_models(RUNTIME_PAYLOAD), updated_by_user_id=None)
    assert stored.source == "catalog"
    assert stored.ids() == EXPECTED_IDS
    persisted = PlatformSettingRepository(db).get_value(svc.CATALOG_SETTING_KEY)
    assert [entry["id"] for entry in persisted["models"]] == list(EXPECTED_IDS)
    assert persisted["runtime_fetched_at"] == "2026-11-04T01:00:00+00:00"
    assert persisted["fetched_at"] == stored.fetched_at

    svc.reset_catalog_state()
    reloaded = svc.current_catalog(db)
    assert reloaded.source == "catalog"
    assert reloaded.ids() == EXPECTED_IDS
    assert reloaded.labels()["mai-code-1.1-flash"] == "MAI-Code-1.1-Flash"
    assert reloaded.runtime_fetched_at == "2026-11-04T01:00:00+00:00"
    assert reloaded.is_fresh() is True
    assert reloaded.payload()["stale"] is False

    # An hour-old copy is still served, but reported stale so a running assistant is asked again.
    svc.store_catalog(db, {**svc.project_runtime_models(RUNTIME_PAYLOAD), "fetched_at": "2026-11-04T01:00:00+00:00"})
    svc.reset_catalog_state()
    old = svc.current_catalog(db)
    assert old.ids() == EXPECTED_IDS
    assert old.is_fresh() is False
    assert old.payload()["stale"] is True


def test_validation_accepts_exactly_what_the_catalog_lists(db):
    svc.store_catalog(db, svc.project_runtime_models(RUNTIME_PAYLOAD))
    svc.reset_catalog_state()  # a cold process: the stored copy is loaded on demand
    assert svc.is_model_allowed("github_copilot", "mai-code-1.1-flash", db=db) is True
    assert svc.is_model_allowed("github_copilot", "gpt-5.3-codex", db=db) is True
    assert svc.is_model_allowed("github_copilot", "gpt-6-sol", db=db) is True
    assert svc.is_model_allowed("github_copilot", "gpt-9-zeta", db=db) is False
    assert svc.is_model_allowed("github_copilot", "claude-sonnet-5.5", db=db) is False
    assert svc.is_model_allowed("ai_platform", "mai-code-1.1-flash", db=db) is False
    assert svc.is_model_allowed("ai_platform", "gpt-5.4", db=db) is True


def test_without_a_catalog_validation_falls_back_to_plausible_ids(db):
    assert svc.is_model_allowed("github_copilot", "gpt-9-zeta", db=db) is True
    assert svc.is_model_allowed("github_copilot", "gpt-5.3-codex", db=db) is False
    assert svc.is_model_allowed("github_copilot", "bogus", db=db) is False
    assert svc.is_model_allowed("github_copilot", "", db=db) is False


@pytest.mark.asyncio
async def test_ensure_catalog_asks_a_running_assistant_once_and_backs_off_after_a_failure(monkeypatch, db):
    calls: list[str] = []

    async def answering(*, user, agent):
        calls.append(agent.id)
        return RUNTIME_PAYLOAD

    monkeypatch.setattr(svc, "fetch_runtime_models", answering)
    user = SimpleNamespace(id=7)

    first = await svc.ensure_catalog(db, user=user, agent=_running_agent(), provider="github_copilot")
    assert first.source == "catalog"
    assert first.ids() == EXPECTED_IDS
    assert calls == ["agent-1"]
    # Fresh: nobody is asked again, whichever assistant loads the picker.
    second = await svc.ensure_catalog(db, user=user, agent=SimpleNamespace(id="agent-2", status="running"), provider="github_copilot")
    assert second.ids() == EXPECTED_IDS
    assert calls == ["agent-1"]
    assert PlatformSettingRepository(db).get(svc.CATALOG_SETTING_KEY).updated_by_user_id == 7

    svc.reset_catalog_state()
    PlatformSettingRepository(db).db.query(type(PlatformSettingRepository(db).get(svc.CATALOG_SETTING_KEY))).delete()
    db.commit()

    async def silent(*, user, agent):
        calls.append("silent:" + agent.id)
        return None

    monkeypatch.setattr(svc, "fetch_runtime_models", silent)
    fallen = await svc.ensure_catalog(db, user=user, agent=_running_agent(), provider="github_copilot")
    assert fallen.source == "static"
    again = await svc.ensure_catalog(db, user=user, agent=_running_agent(), provider="github_copilot")
    assert again.source == "static"
    # One failed ask, then the backoff holds.
    assert calls[-2:] == ["agent-1", "silent:agent-1"]

    svc.reset_catalog_state()
    monkeypatch.setattr(svc, "fetch_runtime_models", answering)
    stopped = await svc.ensure_catalog(db, user=user, agent=SimpleNamespace(id="agent-3", status="stopped"), provider="github_copilot")
    assert stopped.source == "static"
    other = await svc.ensure_catalog(db, user=user, agent=_running_agent(), provider="ai_platform")
    assert other.source == "static"
    assert calls[-1] == "silent:agent-1"


def _client(db):
    from app.main import app
    import app.api.agents as agents_api

    user = User(username="member", password_hash="test", role="user", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    identity = SimpleNamespace(id=user.id, role=user.role, username=user.username, nickname=user.username)
    app.dependency_overrides[agents_api.get_current_user] = lambda: identity
    app.dependency_overrides[agents_api.get_db] = lambda: db
    return TestClient(app), identity


def test_the_catalog_route_serves_the_portal_wide_list(db):
    from app.main import app

    client, _identity = _client(db)
    try:
        before = client.get("/api/models/catalog")
        assert before.status_code == 200
        assert before.json()["source"] == "static"
        assert [entry["id"] for entry in before.json()["models"]] == list(COPILOT_MODELS)

        svc.store_catalog(db, svc.project_runtime_models(RUNTIME_PAYLOAD))
        after = client.get("/api/models/catalog").json()
        assert after["source"] == "catalog"
        assert after["fetched_at"]
        assert after["runtime_fetched_at"] == "2026-11-04T01:00:00+00:00"
        assert after["stale"] is False
        assert after["provider"] == "github_copilot"
        assert [entry["id"] for entry in after["models"]] == list(EXPECTED_IDS)
        assert after["model_labels"]["mai-code-1.1-flash"] == "MAI-Code-1.1-Flash"
    finally:
        app.dependency_overrides.clear()


def test_chat_model_profile_serves_the_same_list_to_every_assistant(monkeypatch, db):
    from app.main import app
    from app.models.runtime_profile import RuntimeProfile

    client, identity = _client(db)
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        calls: list[str] = []

        async def answering(*, user, agent):
            calls.append(agent.id)
            return RUNTIME_PAYLOAD

        monkeypatch.setattr(svc, "fetch_runtime_models", answering)
        profile = RuntimeProfile(
            owner_user_id=identity.id,
            name="default",
            revision=1,
            is_default=True,
            config_json=json.dumps({"llm": {"provider": "github_copilot", "model": "gpt-6-luna"}}),
        )
        db.add(profile)
        db.commit()

        first_agent = client.post("/api/agents", json={"name": "one", "image": "example/image:latest"}).json()["id"]
        second_agent = client.post("/api/agents", json={"name": "two", "image": "example/image:latest"}).json()["id"]

        first = client.get(f"/api/agents/{first_agent}/chat-model-profile").json()
        second = client.get(f"/api/agents/{second_agent}/chat-model-profile").json()
        assert first["available_models"] == list(EXPECTED_IDS)
        assert first["models_source"] == "catalog"
        assert first["models_fetched_at"]
        assert first["model_labels"]["gpt-5.3-codex"] == "GPT-5.3-Codex"
        assert second["available_models"] == first["available_models"]
        assert second["model_labels"] == first["model_labels"]
        # One runtime filled the list; the second assistant did not ask again.
        assert calls == [first_agent]

        # The model the picker offered is the model the chat may use.
        from app.services.inference_settings_service import normalize_agent_inference_overrides
        from app.repositories.agent_repo import AgentRepository

        agent = AgentRepository(db).get_by_id(second_agent)
        assert normalize_agent_inference_overrides(db, agent, {"model_override": "mai-code-1.1-flash"}) == {"model": "mai-code-1.1-flash"}
        assert normalize_agent_inference_overrides(db, agent, {"model_override": "gpt-5.3-codex"}) == {"model": "gpt-5.3-codex"}
        with pytest.raises(ValueError, match="not allowed"):
            normalize_agent_inference_overrides(db, agent, {"model_override": "gpt-9-zeta"})

        # The context sizes follow the model's window: MAI's row says 256k,
        # Codex reports none and keeps the ladder, Luna (the default) is static.
        assert first["model_context_windows"] == {"gpt-6.1-sol": 1_050_000, "mai-code-1.1-flash": 256_000}
        assert first["context_sizes"] == [64_000, 128_000, 256_000, 1_000_000]
        assert normalize_agent_inference_overrides(
            db, agent, {"model_override": "mai-code-1.1-flash", "max_context_tokens": 256_000}
        ) == {"model": "mai-code-1.1-flash", "max_context_tokens": 256_000}
        with pytest.raises(ValueError, match="must be one of: 64000, 128000, 256000$"):
            normalize_agent_inference_overrides(db, agent, {"model_override": "mai-code-1.1-flash", "max_context_tokens": 1_000_000})
        assert normalize_agent_inference_overrides(
            db, agent, {"model_override": "gpt-5.3-codex", "max_context_tokens": 1_000_000}
        )["max_context_tokens"] == 1_000_000
    finally:
        app.dependency_overrides.clear()


def test_context_sizes_follow_the_catalog_windows_and_survive_a_cold_start(db):
    svc.store_catalog(db, svc.project_runtime_models(RUNTIME_PAYLOAD))
    assert svc.current_catalog(db).context_windows() == {"gpt-6.1-sol": 1_050_000, "mai-code-1.1-flash": 256_000}
    assert svc.current_catalog(db).payload()["model_context_windows"]["mai-code-1.1-flash"] == 256_000
    assert model_context_window("github_copilot", "mai-code-1.1-flash") == 256_000
    assert context_size_options("github_copilot", "mai-code-1.1-flash") == (64_000, 128_000, 256_000)
    # Sol's window is above the ladder's top; Codex reports none: the whole ladder.
    assert context_size_options("github_copilot", "gpt-6.1-sol") == (64_000, 128_000, 256_000, 1_000_000)
    assert context_size_options("github_copilot", "gpt-5.3-codex") == (64_000, 128_000, 256_000, 1_000_000)

    # A cold process reads the windows back with the list.
    svc.reset_catalog_state()
    assert context_size_options("github_copilot", "mai-code-1.1-flash") == (64_000, 128_000, 256_000, 1_000_000)
    svc.current_catalog(db)
    assert context_size_options("github_copilot", "mai-code-1.1-flash") == (64_000, 128_000, 256_000)


def test_a_window_between_presets_closes_the_list_and_profile_saves_accept_it():
    from app.schemas.runtime_profile import sanitize_runtime_profile_config_dict
    from app.web import _settings_merge_payload

    register_catalog_models(("gpt-5.3-codex", "tiny-model"), {"gpt-5.3-codex": 400_000, "tiny-model": 32_000})
    assert context_size_options("github_copilot", "gpt-5.3-codex") == (64_000, 128_000, 256_000, 400_000)
    assert context_size_options("github_copilot", "tiny-model") == (32_000,)

    llm = {"provider": "github_copilot", "model": "gpt-5.3-codex", "max_context_tokens": 400_000}
    assert sanitize_runtime_profile_config_dict({"llm": llm})["llm"]["max_context_tokens"] == 400_000
    form = {"__touch_llm": "1", "llm_provider": "github_copilot", "llm_model": "gpt-5.3-codex", "llm_max_context_tokens": "400000"}
    merged, error = _settings_merge_payload({}, form)
    assert error is None
    assert merged["llm"]["max_context_tokens"] == 400_000
    _merged, error = _settings_merge_payload({}, {**form, "llm_max_context_tokens": "1000000"})
    assert error.startswith("Context size must be one of: 64K, 128K, 256K, 400K")

    # Without a catalog the ladder is the rule, as before.
    clear_catalog_models()
    assert "max_context_tokens" not in sanitize_runtime_profile_config_dict({"llm": llm})["llm"]
    merged, error = _settings_merge_payload({}, {**form, "llm_max_context_tokens": "1000000"})
    assert error is None
    assert merged["llm"]["max_context_tokens"] == 1_000_000
