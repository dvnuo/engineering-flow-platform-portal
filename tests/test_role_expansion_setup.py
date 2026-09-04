"""Tests for the onboarding path: assistant types, the connection seed,
per-connection guidance, and the member-facing reading of startup status.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models.assistant_type import AssistantType
from app.models.user import User
from app.models.user_allowlist import UserAllowlistEntry
from app.services.agent_startup_status import startup_view
from app.services.connection_guidance import (
    CONNECTION_GUIDANCE,
    TRACKED_SECTIONS,
    connection_checklist,
)
from app.services.runtime_profile_seed_service import (
    RuntimeProfileSeedService,
    find_secret_fields,
    redact_seed_for_display,
)


def _database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)()


# --------------------------------------------------------------- seed service


def test_seed_stores_a_shared_credential():
    # An organization with a service account puts its token here once instead of
    # asking every member to paste the same one.
    db = _database()
    service = RuntimeProfileSeedService(db)

    service.save_seed(
        {"jira": {"enabled": True, "instances": [{"url": "https://x", "token": "shared-value"}]}}
    )

    assert service.get_seed()["jira"]["instances"][0]["token"] == "shared-value"


def test_seed_keeps_connection_shape():
    db = _database()
    service = RuntimeProfileSeedService(db)

    service.save_seed(
        {
            "jira": {
                "enabled": True,
                "instances": [
                    {"name": "Production", "url": "https://company.atlassian.net", "api_version": "3", "project": "ABC"}
                ],
            }
        }
    )

    seed = service.get_seed()
    instance = seed["jira"]["instances"][0]
    assert instance["url"] == "https://company.atlassian.net"
    assert instance["project"] == "ABC"


def test_seed_drops_unknown_top_level_sections():
    db = _database()
    service = RuntimeProfileSeedService(db)

    service.save_seed({"jira": {"enabled": True}, "not_a_section": {"anything": 1}})

    assert "not_a_section" not in service.get_seed()


def test_a_seed_without_credentials_leaves_them_to_the_member():
    # Credentials are optional. A seed that carries none is the original
    # behaviour: shape only, and every member supplies their own account.
    db = _database()
    service = RuntimeProfileSeedService(db)

    service.save_seed({"jira": {"enabled": True, "instances": [{"url": "https://x"}]}})

    assert not find_secret_fields(service.get_seed())


@pytest.mark.parametrize("field", sorted({"api_key", "password", "token", "api_token", "access_key", "secret"}))
def test_every_sensitive_field_name_is_detected(field):
    assert find_secret_fields({"section": {field: "value"}}) == [f"section.{field}"]


def test_display_copy_masks_secrets_at_any_depth():
    # What the panel prints as the stored value, as opposed to what it puts in
    # the fields the admin can reveal one at a time.
    masked = redact_seed_for_display({"a": {"b": [{"token": "x", "url": "https://keep"}]}})
    assert masked["a"]["b"][0]["token"] == "[REDACTED]"
    assert masked["a"]["b"][0]["url"] == "https://keep"


def test_a_new_member_inherits_a_seeded_credential():
    from app.services.runtime_profile_service import RuntimeProfileService

    db = _database()
    RuntimeProfileSeedService(db).save_seed(
        {"jira": {"enabled": True, "instances": [{"url": "https://x", "token": "shared-value"}]}}
    )
    user = User(username="inheritor", password_hash="hash", role="user", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    profile = RuntimeProfileService(db).ensure_user_has_default_profile(user)

    assert "shared-value" in profile.config_json


def test_new_member_profile_inherits_the_seed(monkeypatch):
    from app.services.runtime_profile_service import RuntimeProfileService

    db = _database()
    RuntimeProfileSeedService(db).save_seed(
        {"jira": {"enabled": True, "instances": [{"name": "Prod", "url": "https://company.atlassian.net"}]}}
    )
    user = User(username="newcomer", password_hash="hash", role="user", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    profile = RuntimeProfileService(db).ensure_user_has_default_profile(user)

    assert "company.atlassian.net" in profile.config_json


def test_unreadable_seed_still_lets_a_member_sign_in(monkeypatch):
    # Onboarding must never be blocked by a seed problem.
    from app.services import runtime_profile_service as module

    db = _database()
    user = User(username="newcomer", password_hash="hash", role="user", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    class Broken:
        def __init__(self, _db):
            raise RuntimeError("seed table missing")

    monkeypatch.setattr(
        "app.services.runtime_profile_seed_service.RuntimeProfileSeedService", Broken
    )
    profile = module.RuntimeProfileService(db).ensure_user_has_default_profile(user)

    assert profile is not None


# --------------------------------------------------------- connection guidance


def test_every_tracked_section_has_guidance():
    for section in TRACKED_SECTIONS:
        assert section in CONNECTION_GUIDANCE, section
        assert CONNECTION_GUIDANCE[section]["steps"]


def test_checklist_counts_only_what_the_member_supplied():
    checklist = connection_checklist(
        {
            "llm": {"provider": "github_copilot", "api_key": "key"},
            "jira": {"enabled": True, "instances": [{"url": "u", "token": ""}]},
            "github": {"enabled": True, "api_token": "t"},
        }
    )

    assert checklist["connected"] == 2
    assert checklist["total"] == 3
    assert checklist["complete"] is False


def test_checklist_omits_a_service_the_team_does_not_use():
    # An unfinishable step reads as a broken setup, so a section nobody enabled
    # never appears.
    checklist = connection_checklist({"llm": {"api_key": "key"}})

    assert [item["section"] for item in checklist["sections"]] == ["llm"]
    assert checklist["complete"] is True


def test_checklist_uses_sections_not_items():
    # Jinja resolves `dict.items` to the built-in method, which silently breaks
    # the template loop. The key must stay renamed.
    assert "sections" in connection_checklist({})
    assert "items" not in connection_checklist({})


def test_ai_platform_credential_counts_as_connected():
    checklist = connection_checklist(
        {"llm": {"provider": "ai_platform", "ai_platform": {"auth": {"username": "u", "password": "p"}}}}
    )

    assert checklist["connected"] == 1


# ------------------------------------------------------------ startup status


def test_missing_profile_secret_points_at_connections():
    view = startup_view("failed", "CreateContainerConfigError: secret efp-profile-abc not found")

    assert view["is_failed"] is True
    assert view["action"] == "open_connections"
    assert "Connections" in view["detail"]


def test_image_pull_failure_is_not_the_members_problem():
    view = startup_view("failed", "ImagePullBackOff")

    assert view["action"] == "contact_support"


def test_unrecognized_failure_still_offers_a_way_forward():
    view = startup_view("failed", "something nobody anticipated")

    assert view["action"] == "retry"
    assert view["technical_detail"] == "something nobody anticipated"


def test_stopped_reads_as_paused_not_broken():
    view = startup_view("stopped")

    assert view["is_failed"] is False
    assert "Paused" in view["headline"]


def test_creating_sets_an_expectation():
    view = startup_view("creating")

    assert view["is_starting"] is True
    assert str(view["typical_seconds"]) in view["detail"]


def test_running_is_ready():
    assert startup_view("running")["phase"] == "ready"


# ----------------------------------------------------------- assistant types


@pytest.fixture()
def admin_client(monkeypatch):
    from app.main import app
    from app.db import get_db
    from app.deps import get_current_user

    db = _database()
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    db.add(admin)
    db.commit()
    db.refresh(admin)
    db.add(UserAllowlistEntry(username="admin", role="admin", is_active=True))
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: admin
    # Not the `with` form: startup runs a schema guard against a database this
    # test intentionally does not migrate.
    client = TestClient(app)
    try:
        yield client, db, admin
    finally:
        app.dependency_overrides.clear()


def test_admin_can_create_and_hide_an_assistant_type(admin_client):
    client, _db, _admin = admin_client

    created = client.post(
        "/api/assistant-types",
        json={"name": "Business Assistant", "icon": "clipboard-list", "skill_branch": "qa"},
    )
    assert created.status_code == 200
    type_id = created.json()["id"]
    assert created.json()["skill_branch"] == "qa"

    listed = client.get("/api/assistant-types")
    assert [item["id"] for item in listed.json()] == [type_id]

    hidden = client.patch(f"/api/assistant-types/{type_id}", json={"is_active": False})
    assert hidden.status_code == 200
    assert client.get("/api/assistant-types").json() == []


def test_blank_branches_are_stored_as_none_so_defaults_apply(admin_client):
    client, _db, _admin = admin_client

    created = client.post(
        "/api/assistant-types",
        json={"name": "Dev Assistant", "agent_settings_branch": "   ", "skill_branch": ""},
    )

    assert created.json()["agent_settings_branch"] is None
    assert created.json()["skill_branch"] is None


def test_type_name_cannot_be_blanked(admin_client):
    client, _db, _admin = admin_client
    type_id = client.post("/api/assistant-types", json={"name": "Ops Assistant"}).json()["id"]

    response = client.patch(f"/api/assistant-types/{type_id}", json={"name": "   "})

    assert response.status_code == 422


def test_simple_create_rejects_an_unknown_type(admin_client):
    client, _db, _admin = admin_client

    response = client.post("/api/agents/simple", json={"name": "Mine", "assistant_type_id": "nope"})

    assert response.status_code == 404


def test_simple_create_rejects_a_hidden_type(admin_client):
    # A type an admin retired should not remain creatable by anyone who kept
    # the page open.
    client, db, _admin = admin_client
    hidden = AssistantType(name="Retired", runtime_type="native", is_active=False)
    db.add(hidden)
    db.commit()
    db.refresh(hidden)

    response = client.post("/api/agents/simple", json={"name": "Mine", "assistant_type_id": hidden.id})

    assert response.status_code == 404
