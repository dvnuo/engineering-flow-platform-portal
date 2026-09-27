"""BrowserStack connector (test, advanced fields) and app packages."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.deps import get_current_user
from app.models import AppPackage, User
from app.models.runtime_profile import RuntimeProfile
from app.services import app_package_service
from app.services.app_package_service import ci_auth_for_url, default_custom_id, sanitize_custom_id
from app.services.connector_registry import get_connector_spec
from app.services.runtime_profile_test_service import RuntimeProfileTestService
from app.services.schema_guard import REQUIRED_PORTAL_TABLES
from app.web import _settings_merge_payload
from tests.test_web_connector_settings import _bind_profile, _build_env


# --- connector ----------------------------------------------------------------


def test_browserstack_connector_offers_a_test_and_the_app_packages_section():
    spec = get_connector_spec("browserstack")
    assert spec.test_targets == ("browserstack",)
    assert spec.panel_extra_template == "partials/connectors/browserstack_extra.html"
    for table in ("app_packages", "mobile_recordings", "mobile_recording_events"):
        assert table in REQUIRED_PORTAL_TABLES


def _mobile_form(**fields):
    form = {"__touch_mobile": "1", "mobile_enabled": "on", "mobile_browserstack_username": "alice"}
    form.update(fields)
    return form


def test_advanced_fields_merge_into_defaults_and_browserstack():
    merged, error = _settings_merge_payload(
        {"mobile-auto": {"browserstack": {"access_key": "keep-me"}}},
        _mobile_form(
            mobile_default_platform="android",
            mobile_network_mode="private-managed",
            mobile_idle_timeout_seconds="300",
            mobile_video__present="1",
            mobile_interactive_debugging__present="1",
            mobile_interactive_debugging="on",
            mobile_browserstack_appium_base_url="https://hub-cloud.browserstack.com/wd/hub/",
            mobile_browserstack_local_mode="managed",
            mobile_browserstack_verify_ssl__present="1",
            mobile_browserstack_verify_ssl="on",
        ),
    )
    assert error is None
    mobile = merged["mobile-auto"]
    assert mobile["defaults"] == {
        "platform": "android",
        "network_mode": "private-managed",
        "idle_timeout_seconds": 300,
        "video": False,
        "interactive_debugging": True,
    }
    bs = mobile["browserstack"]
    assert bs["access_key"] == "keep-me"
    assert bs["appium_base_url"] == "https://hub-cloud.browserstack.com/wd/hub"
    assert bs["local"] == {"mode": "managed"}
    assert bs["verify_ssl"] is True


def test_fields_not_posted_keep_their_stored_values():
    stored = {"mobile-auto": {"defaults": {"idle_timeout_seconds": 200, "video": False}, "browserstack": {"api_base_url": "https://api-cloud.browserstack.com"}}}
    merged, error = _settings_merge_payload(stored, _mobile_form())
    assert error is None
    assert merged["mobile-auto"]["defaults"] == {"idle_timeout_seconds": 200, "video": False}
    assert merged["mobile-auto"]["browserstack"]["api_base_url"] == "https://api-cloud.browserstack.com"


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("mobile_idle_timeout_seconds", "900", "30 to 300"),
        ("mobile_idle_timeout_seconds", "abc", "whole number"),
        ("mobile_browserstack_appium_base_url", "http://hub-cloud.browserstack.com/wd/hub", "https://"),
        ("mobile_browserstack_api_base_url", "https://evil.example.com", "browserstack.com"),
        ("mobile_network_mode", "vpn", "Unsupported"),
    ],
)
def test_invalid_advanced_values_are_rejected(field, value, message):
    _, error = _settings_merge_payload({}, _mobile_form(**{field: value}))
    assert error and message in error


def test_browserstack_connection_test_reports_parallel_headroom(monkeypatch):
    service = RuntimeProfileTestService()
    seen = {}

    async def fake_request(method, url, headers, timeout, json_payload=None):
        seen["url"] = url
        seen["auth"] = headers.get("Authorization", "")
        return True, "ok", {"parallel_sessions_max_allowed": 5, "parallel_sessions_running": 2, "queued_sessions": 0}

    monkeypatch.setattr(service, "_http_request", fake_request)
    config = {"mobile-auto": {"enabled": True, "browserstack": {"username": "alice", "access_key": "k"}}}
    ok, message = asyncio.run(service.run_test("browserstack", config))
    assert ok and "2 of 5 parallel sessions in use" in message
    assert seen["url"] == "https://api-cloud.browserstack.com/app-automate/plan.json"
    assert seen["auth"].startswith("Basic ")

    ok, message = asyncio.run(service.run_test("browserstack", {"mobile-auto": {"enabled": True}}))
    assert not ok and "username and access key" in message

    async def refused(method, url, headers, timeout, json_payload=None):
        return False, "HTTP 401: HTTP Basic: Access denied.", None

    monkeypatch.setattr(service, "_http_request", refused)
    ok, message = asyncio.run(service.run_test("browserstack", config))
    assert not ok and message.startswith("BrowserStack refused this username and access key") and "HTTP 401" in message


def test_browserstack_panel_renders_test_advanced_and_app_packages(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {"mobile-auto": {"enabled": True, "browserstack": {"username": "alice", "access_key": "k"}}})
        html = env.client.get("/app/connectors/browserstack/panel").text
        assert 'data-test-target="browserstack"' in html
        assert 'name="mobile_idle_timeout_seconds"' in html
        assert "data-app-packages" in html
        # The app packages section sits outside the settings form.
        assert html.index("data-app-packages") > html.index("</form>")
        # Without a hosted Inspector the page points at the desktop app.
        assert "appium-inspector/releases" in html
    finally:
        env.cleanup()


# --- test secrets -------------------------------------------------------------


def test_test_secret_rows_merge_in_page_order_and_blank_rows_drop():
    merged, error = _settings_merge_payload(
        {"mobile-auto": {"test_secrets": [{"name": "OLD_SECRET", "secret": "gone"}]}},
        _mobile_form(**{
            "mobile_test_secrets__present": "1",
            "mobile_test_secrets_1_name": "MOBILE_SECRET_PIN",
            "mobile_test_secrets_1_secret": "246810",
            "mobile_test_secrets_0_name": "MOBILE_SECRET_PASSWORD",
            "mobile_test_secrets_0_secret": " Uat pass ",
            "mobile_test_secrets_2_name": "",
            "mobile_test_secrets_2_secret": "",
        }),
    )
    assert error is None
    # Values are kept exactly as typed; a removed row is gone.
    assert merged["mobile-auto"]["test_secrets"] == [
        {"name": "MOBILE_SECRET_PASSWORD", "secret": " Uat pass "},
        {"name": "MOBILE_SECRET_PIN", "secret": "246810"},
    ]


def test_test_secrets_not_posted_are_kept_and_an_empty_list_removes_them():
    stored = {"mobile-auto": {"test_secrets": [{"name": "MOBILE_SECRET_PASSWORD", "secret": "pw"}]}}
    merged, error = _settings_merge_payload(stored, _mobile_form())
    assert error is None and merged["mobile-auto"]["test_secrets"] == [{"name": "MOBILE_SECRET_PASSWORD", "secret": "pw"}]
    merged, error = _settings_merge_payload(stored, _mobile_form(mobile_test_secrets__present="1"))
    assert error is None and "test_secrets" not in merged["mobile-auto"]


@pytest.mark.parametrize(
    "rows, message",
    [
        ({"mobile_test_secrets_0_name": "", "mobile_test_secrets_0_secret": "pw"}, "Give every test secret a name"),
        ({"mobile_test_secrets_0_name": "password", "mobile_test_secrets_0_secret": "pw"}, "capital letters"),
        ({"mobile_test_secrets_0_name": "MOBILE_SECRET_PASSWORD", "mobile_test_secrets_0_secret": "  "}, "Enter a value"),
        (
            {
                "mobile_test_secrets_0_name": "MOBILE_SECRET_PASSWORD",
                "mobile_test_secrets_0_secret": "a",
                "mobile_test_secrets_1_name": "MOBILE_SECRET_PASSWORD",
                "mobile_test_secrets_1_secret": "b",
            },
            "listed twice",
        ),
    ],
)
def test_invalid_test_secret_rows_are_rejected(rows, message):
    _, error = _settings_merge_payload({}, _mobile_form(mobile_test_secrets__present="1", **rows))
    assert error and message in error


def test_test_secrets_are_sanitized_redacted_and_encrypted(monkeypatch):
    from app.schemas.runtime_profile import (
        redact_runtime_profile_config_for_public_response,
        sanitize_runtime_profile_config_dict,
    )
    from app.services.profile_secret_encryption import decrypt_sensitive_fields, encrypt_sensitive_fields

    config = sanitize_runtime_profile_config_dict({
        "mobile-auto": {
            "enabled": True,
            "test_secrets": [
                {"name": "MOBILE_SECRET_PASSWORD", "secret": "pw-1"},
                {"name": "MOBILE_SECRET_PASSWORD", "secret": "duplicate"},
                {"name": "lower_case", "secret": "x"},
                {"name": "MOBILE_SECRET_EMPTY", "secret": ""},
                "not-a-row",
            ],
        }
    })
    assert config["mobile-auto"]["test_secrets"] == [{"name": "MOBILE_SECRET_PASSWORD", "secret": "pw-1"}]

    public = redact_runtime_profile_config_for_public_response(config)
    assert public["mobile-auto"]["test_secrets"] == [{"name": "MOBILE_SECRET_PASSWORD", "secret_present": True}]
    assert "pw-1" not in json.dumps(public)

    monkeypatch.setenv("EFP_CONFIG_KEY", "test-key")
    encrypted = encrypt_sensitive_fields(config)
    assert encrypted["mobile-auto"]["test_secrets"][0]["secret"].startswith("ENC:")
    assert encrypted["mobile-auto"]["test_secrets"][0]["name"] == "MOBILE_SECRET_PASSWORD"
    assert decrypt_sensitive_fields(encrypted) == config


def test_browserstack_panel_renders_test_secret_rows(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {"mobile-auto": {"enabled": True, "test_secrets": [{"name": "MOBILE_SECRET_PASSWORD", "secret": "pw"}]}})
        html = env.client.get("/app/connectors/browserstack/panel").text
        assert 'name="mobile_test_secrets__present"' in html
        assert 'name="mobile_test_secrets_0_name" value="MOBILE_SECRET_PASSWORD"' in html
        assert 'type="password" name="mobile_test_secrets_0_secret"' in html
        assert 'data-action="add-test-secret"' in html
    finally:
        env.cleanup()


# --- app packages -------------------------------------------------------------


@pytest.mark.parametrize(
    "name, platform, expected",
    [
        ("fxapp-1.4.2-uat.apk", "android", "fxapp-uat-android"),
        ("FXApp_2026.09.27.ipa", "ios", "fxapp-ios"),
        ("bank-android.aab", "android", "bank-android"),
    ],
)
def test_default_custom_id_drops_versions(name, platform, expected):
    assert default_custom_id(name, platform) == expected


def test_sanitize_custom_id():
    assert sanitize_custom_id(" my app/uat ") == "my-app-uat"
    assert sanitize_custom_id("") is None


def test_ci_auth_only_matches_configured_instances():
    config = {
        "jenkins": {"enabled": True, "instances": [{"url": "https://ci.corp/", "username": "u", "token": "t"}]},
        "nexus": {"enabled": True, "instances": [{"url": "https://nexus.corp/repository"}]},
    }
    assert ci_auth_for_url(config, "https://ci.corp/job/app/lastSuccessfulBuild/artifact/app.apk") == ("u", "t")
    assert ci_auth_for_url(config, "https://nexus.corp/repository/apps/app.ipa") is True
    assert ci_auth_for_url(config, "https://ci.corp.evil.com/app.apk") is None
    assert ci_auth_for_url(config, "http://169.254.169.254/latest/meta-data") is None


@pytest.fixture
def api(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    alice = User(username="alice", password_hash="x", role="user", is_active=True)
    session.add(alice)
    session.commit()
    session.add(RuntimeProfile(
        owner_user_id=alice.id,
        name="alice",
        config_json=json.dumps({
            "mobile-auto": {"enabled": True, "browserstack": {"username": "alice", "access_key": "k"}},
            "jenkins": {"enabled": True, "instances": [{"url": "https://ci.corp", "username": "u", "token": "t"}]},
        }),
        revision=1,
        is_default=True,
    ))
    session.commit()

    calls = {"file": [], "url": [], "deleted": [], "download": []}

    async def fake_upload_file(account, path, file_name, custom_id):
        with open(path, "rb") as fh:
            calls["file"].append((account.username, file_name, custom_id, fh.read()))
        return {"app_url": "bs://abc123", "custom_id": custom_id}

    async def fake_upload_url(account, url, custom_id):
        calls["url"].append((url, custom_id))
        return {"app_url": "bs://fromurl", "custom_id": custom_id}

    async def fake_delete(account, app_url):
        calls["deleted"].append(app_url)

    async def fake_download(self, url, auth):
        calls["download"].append((url, auth))
        async def chunks():
            yield b"PK-apk-bytes"
        return await app_package_service.spool_stream(chunks(), self.limit_bytes, ".apk")

    monkeypatch.setattr(app_package_service, "upload_file_to_browserstack", fake_upload_file)
    monkeypatch.setattr(app_package_service, "upload_url_to_browserstack", fake_upload_url)
    monkeypatch.setattr(app_package_service, "delete_browserstack_app", fake_delete)
    monkeypatch.setattr(app_package_service.AppPackageService, "_download", fake_download)

    from app.main import app

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: alice
    try:
        yield TestClient(app), session, alice, calls
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        session.close()


def test_upload_streams_the_file_to_browserstack_and_lists_it(api):
    client, session, alice, calls = api
    resp = client.post(
        "/api/app-packages?note=UAT%201.4.2",
        content=b"PK-apk-bytes",
        headers={"X-File-Name": "fxapp-1.4.2-uat.apk", "Content-Type": "application/octet-stream"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["app_url"] == "bs://abc123"
    assert body["custom_id"] == "fxapp-uat-android"
    assert body["platform"] == "android" and body["size_bytes"] == len(b"PK-apk-bytes")
    assert body["days_left"] in (29, 30) and not body["expired"]
    assert calls["file"] == [("alice", "fxapp-1.4.2-uat.apk", "fxapp-uat-android", b"PK-apk-bytes")]

    listed = client.get("/api/app-packages").json()
    assert [p["app_url"] for p in listed["packages"]] == ["bs://abc123"]
    assert listed["packages"][0]["note"] == "UAT 1.4.2"

    # The same build again keeps one row.
    client.post("/api/app-packages", content=b"PK-apk-bytes", headers={"X-File-Name": "fxapp-1.4.2-uat.apk"})
    assert session.query(AppPackage).count() == 1


def test_upload_rejects_other_files_and_oversized_bodies(api):
    client, _, _, calls = api
    resp = client.post("/api/app-packages", content=b"x", headers={"X-File-Name": "notes.txt"})
    assert resp.status_code == 415
    resp = client.post("/api/app-packages", content=b"x", headers={"X-File-Name": "a.apk", "Content-Length": str(10 * 1024 * 1024 * 1024)})
    assert resp.status_code == 413
    assert calls["file"] == []


def test_upload_needs_an_enabled_connector_before_reading_the_body(api):
    client, session, alice, calls = api
    profile = session.query(RuntimeProfile).filter_by(owner_user_id=alice.id).one()
    profile.config_json = json.dumps({"mobile-auto": {"enabled": False}})
    session.commit()
    resp = client.post("/api/app-packages", content=b"PK", headers={"X-File-Name": "a.apk"})
    assert resp.status_code == 409 and "Turn the BrowserStack connector on" in resp.json()["detail"]
    assert calls["file"] == []


def test_from_url_fetches_ci_builds_itself_and_hands_others_to_browserstack(api):
    client, _, _, calls = api
    resp = client.post("/api/app-packages/from-url", json={"url": "https://ci.corp/job/app/1/artifact/app.apk"})
    assert resp.status_code == 201, resp.text
    assert calls["download"] == [("https://ci.corp/job/app/1/artifact/app.apk", ("u", "t"))]
    assert calls["file"][0][1] == "app.apk"

    resp = client.post("/api/app-packages/from-url", json={"url": "https://downloads.example.com/app.ipa", "custom_id": "fx"})
    assert resp.status_code == 201, resp.text
    assert calls["url"] == [("https://downloads.example.com/app.ipa", "fx")]
    assert len(calls["download"]) == 1, "Portal must not fetch URLs outside the member's CI connectors"

    resp = client.post("/api/app-packages/from-url", json={"url": "http://internal.corp/app.apk"})
    assert resp.status_code == 400


def test_delete_removes_it_from_browserstack_and_portal(api):
    client, session, _, calls = api
    created = client.post("/api/app-packages", content=b"PK", headers={"X-File-Name": "a.apk"}).json()
    resp = client.delete(f"/api/app-packages/{created['id']}")
    assert resp.status_code == 200
    assert calls["deleted"] == ["bs://abc123"]
    assert session.query(AppPackage).count() == 0
    assert client.delete("/api/app-packages/nope").status_code == 404
