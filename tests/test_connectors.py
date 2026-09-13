"""Connectors: registry, service, API, proxy injection, and schema wiring."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.deps import get_current_user
from app.models import AuditLog, User, UserConnector
from app.services import connector_service
from app.services.connector_registry import (
    CONNECTOR_REGISTRY,
    LOCAL_BROWSER_TYPE,
    get_connector_spec,
    list_connector_specs,
)
from app.services.schema_guard import REQUIRED_PORTAL_TABLES


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def users(db_session):
    alice = User(username="alice", password_hash="hash", role="user", is_active=True)
    bob = User(username="bob", password_hash="hash", role="user", is_active=True)
    db_session.add_all([alice, bob])
    db_session.commit()
    return alice, bob


def _client(db_session, user):
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def alice_client(db_session, users):
    yield from _client(db_session, users[0])


# ---------------------------------------------------------------------------
# registry


def test_registry_has_local_browser_with_defaults():
    spec = get_connector_spec(LOCAL_BROWSER_TYPE)
    assert spec.kind == "local"
    assert spec.panel_template.endswith("connector_local_browser_panel.html")
    assert spec.normalized_config(None) == {"auto_enable_in_new_chats": True, "preferred_port": 8765}
    assert [item.type for item in list_connector_specs()] == list(CONNECTOR_REGISTRY)
    with pytest.raises(KeyError):
        get_connector_spec("nope")


@pytest.mark.parametrize(
    "config, message",
    [
        ({"preferred_port": 80}, "preferred_port"),
        ({"preferred_port": "abc"}, "preferred_port"),
        ({"preferred_port": True}, "preferred_port"),
        ({"auto_enable_in_new_chats": 3}, "auto_enable_in_new_chats"),
        ({"surprise": 1}, "Unknown"),
    ],
)
def test_registry_rejects_bad_local_browser_config(config, message):
    with pytest.raises(ValueError) as excinfo:
        get_connector_spec(LOCAL_BROWSER_TYPE).normalized_config(config)
    assert message in str(excinfo.value)


def test_registry_coerces_string_values():
    normalized = get_connector_spec(LOCAL_BROWSER_TYPE).normalized_config(
        {"auto_enable_in_new_chats": "false", "preferred_port": "8766"}
    )
    assert normalized == {"auto_enable_in_new_chats": False, "preferred_port": 8766}


# ---------------------------------------------------------------------------
# service


def test_service_lists_every_type_with_defaults_when_no_row(db_session, users):
    alice, _ = users
    entries = connector_service.list_for_user(db_session, alice)
    assert [item["type"] for item in entries] == [LOCAL_BROWSER_TYPE]
    assert entries[0]["enabled"] is False
    assert entries[0]["config"] == {"auto_enable_in_new_chats": True, "preferred_port": 8765}
    assert entries[0]["last_verified_at"] is None
    assert connector_service.enabled_connectors_for_user(db_session, alice.id) == {}


def test_service_update_persists_validates_and_audits(db_session, users):
    alice, bob = users
    entry = connector_service.update_for_user(
        db_session,
        alice,
        LOCAL_BROWSER_TYPE,
        enabled=True,
        config={"auto_enable_in_new_chats": False, "preferred_port": 8766},
    )
    assert entry["enabled"] is True
    assert entry["config"] == {"auto_enable_in_new_chats": False, "preferred_port": 8766}

    row = db_session.query(UserConnector).filter_by(owner_user_id=alice.id).one()
    assert json.loads(row.config_json) == {"auto_enable_in_new_chats": False, "preferred_port": 8766}
    assert connector_service.enabled_connectors_for_user(db_session, alice.id) == {
        LOCAL_BROWSER_TYPE: {"auto_enable_in_new_chats": False, "preferred_port": 8766}
    }
    # Bob's view is untouched by Alice's row.
    assert connector_service.get_for_user(db_session, bob, LOCAL_BROWSER_TYPE)["enabled"] is False
    assert connector_service.enabled_connectors_for_user(db_session, bob.id) == {}

    audit = db_session.query(AuditLog).filter_by(action="update_connector").one()
    assert audit.user_id == alice.id
    assert audit.target_type == "connector"
    assert audit.target_id == LOCAL_BROWSER_TYPE
    assert json.loads(audit.details_json)["enabled"] is True

    with pytest.raises(ValueError):
        connector_service.update_for_user(db_session, alice, LOCAL_BROWSER_TYPE, enabled=True, config={"preferred_port": 1})

    connector_service.update_for_user(db_session, alice, LOCAL_BROWSER_TYPE, enabled=False, config={})
    assert connector_service.enabled_connectors_for_user(db_session, alice.id) == {}


def test_service_records_verification_only_when_ok(db_session, users):
    alice, _ = users
    failed = connector_service.record_verification(db_session, alice, LOCAL_BROWSER_TYPE, ok=False, details={"code": "x"})
    assert failed == {"ok": False, "last_verified_at": None}
    passed = connector_service.record_verification(db_session, alice, LOCAL_BROWSER_TYPE, ok=True, details={"port": 8765})
    assert passed["ok"] is True
    assert passed["last_verified_at"]
    entry = connector_service.get_for_user(db_session, alice, LOCAL_BROWSER_TYPE)
    assert entry["last_verified_at"] == passed["last_verified_at"]
    # Verification never flips the enabled flag.
    assert entry["enabled"] is False


def test_service_tolerates_corrupt_stored_config(db_session, users):
    alice, _ = users
    db_session.add(UserConnector(owner_user_id=alice.id, connector_type=LOCAL_BROWSER_TYPE, enabled=True, config_json="{not json"))
    db_session.commit()
    entry = connector_service.get_for_user(db_session, alice, LOCAL_BROWSER_TYPE)
    assert entry["config"] == {"auto_enable_in_new_chats": True, "preferred_port": 8765}
    assert connector_service.enabled_connectors_for_user(db_session, alice.id)[LOCAL_BROWSER_TYPE]["preferred_port"] == 8765


def test_download_url_prefers_configuration():
    class _Settings:
        local_browser_cli_download_url = ""

    assert connector_service.local_browser_download_url(_Settings()) == "/static/downloads/efp-browser-bridge.zip"
    _Settings.local_browser_cli_download_url = "https://artifacts.example.test/bridge.zip"
    assert connector_service.local_browser_download_url(_Settings()) == "https://artifacts.example.test/bridge.zip"


# ---------------------------------------------------------------------------
# API


def test_connectors_api_round_trip(alice_client):
    listing = alice_client.get("/api/connectors")
    assert listing.status_code == 200
    assert [item["type"] for item in listing.json()] == [LOCAL_BROWSER_TYPE]
    assert listing.json()[0]["enabled"] is False

    updated = alice_client.put(
        f"/api/connectors/{LOCAL_BROWSER_TYPE}",
        json={"enabled": True, "config": {"auto_enable_in_new_chats": True, "preferred_port": 8767}},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["enabled"] is True
    assert updated.json()["config"]["preferred_port"] == 8767

    single = alice_client.get(f"/api/connectors/{LOCAL_BROWSER_TYPE}")
    assert single.status_code == 200
    assert single.json()["enabled"] is True

    verified = alice_client.post(f"/api/connectors/{LOCAL_BROWSER_TYPE}/verify", json={"ok": True, "details": {"tab_count": 2}})
    assert verified.status_code == 200
    assert verified.json()["ok"] is True
    assert verified.json()["last_verified_at"]

    bad = alice_client.put(f"/api/connectors/{LOCAL_BROWSER_TYPE}", json={"enabled": True, "config": {"preferred_port": 1}})
    assert bad.status_code == 400
    assert "preferred_port" in bad.json()["detail"]

    assert alice_client.get("/api/connectors/unknown").status_code == 404
    assert alice_client.put("/api/connectors/unknown", json={"enabled": True}).status_code == 404
    assert alice_client.post("/api/connectors/unknown/verify", json={"ok": True}).status_code == 404


def test_connectors_api_hidden_when_feature_disabled(alice_client, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "connectors_enabled", False)
    assert alice_client.get("/api/connectors").status_code == 404
    assert alice_client.put(f"/api/connectors/{LOCAL_BROWSER_TYPE}", json={"enabled": True}).status_code == 404


def test_connector_panel_renders_guided_steps(db_session, users, monkeypatch):
    from app import web
    from app.main import app

    alice, _ = users
    monkeypatch.setattr(web, "_current_user_from_cookie", lambda request: alice)
    monkeypatch.setattr(web, "SessionLocal", lambda: db_session)
    original_close = db_session.close
    monkeypatch.setattr(db_session, "close", lambda: None)
    try:
        client = TestClient(app)
        response = client.get(f"/app/connectors/{LOCAL_BROWSER_TYPE}/panel")
        assert response.status_code == 200, response.text
        html = response.text
        assert 'id="connector-panel-root"' in html
        assert "Step 1" in html and "Step 4" in html
        assert "efp-browser-bridge.zip" in html
        assert "install-bridge.cmd" in html
        assert 'data-connector-action="test"' in html
        assert client.get("/app/connectors/unknown/panel").status_code == 404
    finally:
        monkeypatch.setattr(db_session, "close", original_close)


def test_app_page_exposes_connectors_menu_when_enabled():
    from pathlib import Path

    html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert 'id="connectors-menu-btn"' in html
    assert 'id="connectors-nav-section"' in html
    assert 'id="composer-browser-toggle"' in html
    assert "js/connectors/local_browser.js" in html


# ---------------------------------------------------------------------------
# proxy injection (CONNECTORS_CONTRACT §2)


def _injected(monkeypatch, payload, enabled):
    from app.api import proxy
    from app.services import connector_service as service_module

    monkeypatch.setattr(service_module, "enabled_connectors_for_user", lambda db, user_id: enabled)
    user = type("U", (), {"id": 7})()
    return proxy._inject_connectors_metadata(payload, db=None, user=user)


def test_proxy_injects_only_enabled_connectors(monkeypatch):
    payload = {
        "message": "hi",
        "metadata": {"portal_user": {"id": "7"}},
        "connectors": {
            "local_browser": {"client_id": "tab-7f3a9c", "protocol_version": 1},
            "other": {"client_id": "tab-7f3a9c"},
        },
    }
    result = _injected(monkeypatch, payload, {"local_browser": {"auto_enable_in_new_chats": True, "preferred_port": 8765}})
    assert "connectors" not in result  # the client hint never reaches the runtime as-is
    assert result["metadata"]["connectors"] == {
        "local_browser": {
            "enabled": True,
            "client_id": "tab-7f3a9c",
            "protocol_version": 1,
            "config": {"auto_enable_in_new_chats": True, "preferred_port": 8765},
        }
    }
    assert result["metadata"]["enable_browser_tool"] is True
    assert result["metadata"]["portal_user"] == {"id": "7"}


def test_proxy_drops_connectors_the_member_has_not_enabled(monkeypatch):
    payload = {"metadata": {}, "connectors": {"local_browser": {"client_id": "tab-1"}}}
    result = _injected(monkeypatch, payload, {})
    assert "connectors" not in result["metadata"]
    assert "enable_browser_tool" not in result["metadata"]


def test_proxy_rejects_bad_client_ids_and_spoofed_metadata(monkeypatch):
    payload = {
        "metadata": {"connectors": {"local_browser": {"enabled": True, "client_id": "spoof"}}, "enable_browser_tool": True},
        "connectors": {"local_browser": {"client_id": "not valid!"}},
    }
    result = _injected(monkeypatch, payload, {"local_browser": {}})
    assert "connectors" not in result["metadata"]
    assert "enable_browser_tool" not in result["metadata"]


def test_proxy_survives_service_failure(monkeypatch):
    from app.api import proxy
    from app.services import connector_service as service_module

    def _boom(db, user_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(service_module, "enabled_connectors_for_user", _boom)
    payload = {"metadata": {}, "connectors": {"local_browser": {"client_id": "tab-1"}}}
    result = proxy._inject_connectors_metadata(payload, db=None, user=type("U", (), {"id": 1})())
    assert result["metadata"] == {}


# ---------------------------------------------------------------------------
# schema wiring


def test_schema_guard_and_migration_cover_user_connectors():
    from pathlib import Path

    assert "user_connectors" in REQUIRED_PORTAL_TABLES
    migration = Path("alembic/versions/20260913_0035_add_user_connectors.py").read_text(encoding="utf-8")
    assert 'down_revision = "20260828_0034"' in migration
    assert 'create_table(\n            "user_connectors"' in migration or 'create_table("user_connectors"' in migration.replace("\n", "").replace(" ", "")


def test_help_center_lists_local_browser_connector_topic():
    from app.services.help_center import get_topic, topics_by_group

    topic = get_topic("local-browser-connector")
    assert topic is not None
    assert topic.group == "Connectors"
    assert topic.steps
    groups = dict(topics_by_group())
    assert "Connectors" in groups and any(item.id == "local-browser-connector" for item in groups["Connectors"])
