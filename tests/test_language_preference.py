"""The member's language: a server-side preference that renders the page and steers the assistant.

Two settings live in ``users.preferences_json`` (migration 0042): the Portal
language (``auto`` follows the browser) and the assistant's reply language
(``follow`` means the same as the Portal's). ``app/services/locale_service.py``
resolves them per request; ``GET /api/auth/me`` and
``PUT /api/auth/me/preferences`` read and write them from the account menu;
``base.html`` renders ``<html lang>`` from the result; and the chat proxy
writes ``reply_language`` into the trusted ``portal_user`` metadata of every
request that makes the runtime write member-visible text (chat posts, card
answers, message edits, title generation), which the runtimes turn into a
"Reply language" prompt block.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from _js_extract_helpers import _extract_js_function
from app.api.proxy import _session_turn_request_session_id
from app.db import Base
from app.models import User, UserAllowlistEntry
from app.services.locale_service import (
    LocalePreferences,
    locale_for_language_tag,
    locale_from_accept_language,
    locale_preferences_from_json,
    merge_locale_preferences,
    resolve_locale,
)
from app.services.proxy_service import build_portal_user_metadata

REPO = Path(__file__).resolve().parents[1]
APP_HTML = (REPO / "app" / "templates" / "app.html").read_text(encoding="utf-8")
BASE_HTML = (REPO / "app" / "templates" / "base.html").read_text(encoding="utf-8")
CHAT_JS = (REPO / "app" / "static" / "js" / "chat_ui.js").read_text(encoding="utf-8")
CSS = (REPO / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")
CONTRACT = (REPO / "docs" / "PORTAL_RUNTIME_CONTRACT.md").read_text(encoding="utf-8")


# --- resolving the language ---------------------------------------------------

@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("en", "en"),
        ("en-US", "en"),
        ("EN-gb", "en"),
        # Simplified: a bare zh, the mainland and Singapore regions, the script.
        ("zh", "zh-Hans"),
        ("zh-CN", "zh-Hans"),
        ("zh-SG", "zh-Hans"),
        ("zh-Hans", "zh-Hans"),
        ("zh-Hans-CN", "zh-Hans"),
        # Traditional means Hong Kong conventions, the only variant shipped.
        ("zh-HK", "zh-Hant-HK"),
        ("zh-MO", "zh-Hant-HK"),
        ("zh-TW", "zh-Hant-HK"),
        ("zh-Hant", "zh-Hant-HK"),
        ("zh-Hant-TW", "zh-Hant-HK"),
        ("zh_Hant_HK", "zh-Hant-HK"),
        # Cantonese is written with the same characters.
        ("yue", "zh-Hant-HK"),
        ("yue-Hant-HK", "zh-Hant-HK"),
        ("ja", None),
        ("fr-FR", None),
        ("", None),
        ("*", None),
    ],
)
def test_language_tags_map_to_the_three_locales(tag, expected):
    assert locale_for_language_tag(tag) == expected


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("zh-HK,zh;q=0.9,en;q=0.8", "zh-Hant-HK"),
        # Quality values win over the header's order.
        ("zh-HK;q=0.8, en;q=0.9", "en"),
        ("en-US,en;q=0.9,zh-CN;q=0.8", "en"),
        # Unsupported languages are skipped, not treated as English.
        ("fr, zh-CN;q=0.5", "zh-Hans"),
        ("fr-FR,fr;q=0.9", None),
        ("zh-HK;q=0", None),
        # An unreadable weight counts as zero.
        ("zh-TW;q=abc, en", "en"),
        ("*", None),
        ("", None),
        (None, None),
    ],
)
def test_accept_language_picks_the_first_supported_locale(header, expected):
    assert locale_from_accept_language(header) == expected


def test_saved_preferences_are_read_defensively():
    assert locale_preferences_from_json(None) == LocalePreferences()
    assert locale_preferences_from_json("") == LocalePreferences()
    assert locale_preferences_from_json("not json") == LocalePreferences()
    assert locale_preferences_from_json("[1, 2]") == LocalePreferences()
    # A value this Portal does not ship reads as the default, never as itself.
    assert locale_preferences_from_json('{"ui_locale": "fr"}') == LocalePreferences()
    assert locale_preferences_from_json('{"ui_locale": "zh-Hant-HK"}') == LocalePreferences(ui_locale="zh-Hant-HK")
    # There is one language setting: a separate reply language is not read.
    assert locale_preferences_from_json({"reply_language": "en"}) == LocalePreferences()


def test_merging_keeps_unknown_keys_and_refuses_unknown_values():
    stored = merge_locale_preferences('{"theme": "dark", "reply_language": "zh-Hans"}', ui_locale="en")
    assert json.loads(stored) == {"reply_language": "zh-Hans", "theme": "dark", "ui_locale": "en"}
    # None leaves a key alone; a broken stored value starts from scratch.
    assert json.loads(merge_locale_preferences(stored, ui_locale=None)) == json.loads(stored)
    assert json.loads(merge_locale_preferences("garbage", ui_locale="auto")) == {"ui_locale": "auto"}
    with pytest.raises(ValueError):
        merge_locale_preferences(stored, ui_locale="fr")
    # A key this Portal does not have is neither saved nor validated.
    assert json.loads(merge_locale_preferences(stored, reply_language="yue-Hant-HK")) == json.loads(stored)


@pytest.mark.parametrize(
    ("preferences", "header", "expected"),
    [
        # Nothing saved, nothing asked: English, derived.
        (LocalePreferences(), None, ("en", "default", "en", False)),
        # Nothing saved: the browser decides, and the assistant follows it.
        (LocalePreferences(), "zh-HK", ("zh-Hant-HK", "browser", "zh-Hant-HK", False)),
        # A language the member chose beats the browser, and the assistant
        # always answers in it.
        (LocalePreferences(ui_locale="en"), "zh-HK", ("en", "preference", "en", True)),
        (LocalePreferences(ui_locale="zh-Hans"), None, ("zh-Hans", "preference", "zh-Hans", True)),
        # Auto is the browser's language, as a default.
        (LocalePreferences(ui_locale="auto"), "zh-CN", ("zh-Hans", "browser", "zh-Hans", False)),
    ],
)
def test_resolution_order(preferences, header, expected):
    resolved = resolve_locale(preferences, header)
    assert (resolved.ui_locale, resolved.ui_source, resolved.reply_language, resolved.reply_language_strict) == expected
    assert resolved.as_dict() == {
        "ui_locale": expected[0],
        "ui_source": expected[1],
        "reply_language": expected[2],
        "reply_language_strict": expected[3],
    }


def test_portal_user_metadata_carries_the_reply_language_only_when_given():
    user = SimpleNamespace(id=7, username="jdoe", nickname="Jane")
    assert build_portal_user_metadata(user) == {"id": "7", "username": "jdoe", "display_name": "Jane"}
    resolved = resolve_locale(LocalePreferences(ui_locale="zh-Hant-HK"), None)
    assert build_portal_user_metadata(user, reply_language=resolved) == {
        "id": "7",
        "username": "jdoe",
        "display_name": "Jane",
        "reply_language": "zh-Hant-HK",
        "reply_language_strict": True,
    }


# --- the API ------------------------------------------------------------------

def _database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)()


def _signed_in_api_client():
    from app.db import get_db
    from app.deps import get_current_user
    from app.main import app

    db = _database()
    user = User(username="jdoe", nickname="Jane Doe", password_hash="hash", role="user", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    def _override_user():
        return user

    def _override_db():
        yield db

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), db, user, _cleanup


def test_me_reports_the_saved_preferences_and_what_this_request_resolved_to():
    client, db, user, cleanup = _signed_in_api_client()
    try:
        me = client.get("/api/auth/me", headers={"Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8"})
        assert me.status_code == 200
        assert me.json()["preferences"] == {"ui_locale": "auto"}
        assert me.json()["locale"] == {
            "ui_locale": "zh-Hant-HK",
            "ui_source": "browser",
            "reply_language": "zh-Hant-HK",
            "reply_language_strict": False,
        }

        saved = client.put("/api/auth/me/preferences", json={"ui_locale": "en"}, headers={"Accept-Language": "zh-HK"})
        assert saved.status_code == 200
        assert saved.json()["preferences"] == {"ui_locale": "en"}
        # A language the member chose is the assistant's too, and strict.
        assert saved.json()["locale"] == {
            "ui_locale": "en",
            "ui_source": "preference",
            "reply_language": "en",
            "reply_language_strict": True,
        }
        db.refresh(user)
        assert json.loads(user.preferences_json) == {"ui_locale": "en"}

        saved = client.put("/api/auth/me/preferences", json={"ui_locale": "zh-Hans"})
        assert saved.status_code == 200
        assert saved.json()["preferences"] == {"ui_locale": "zh-Hans"}
        assert saved.json()["locale"]["reply_language"] == "zh-Hans"
        assert saved.json()["locale"]["reply_language_strict"] is True
        db.refresh(user)
        assert json.loads(user.preferences_json) == {"ui_locale": "zh-Hans"}

        # Values this Portal does not ship are refused, and nothing changes;
        # a setting it no longer has is ignored.
        assert client.put("/api/auth/me/preferences", json={"ui_locale": "yue-Hant-HK"}).status_code == 422
        assert client.put("/api/auth/me/preferences", json={"reply_language": "en"}).status_code == 200
        db.refresh(user)
        assert json.loads(user.preferences_json) == {"ui_locale": "zh-Hans"}

        # The rest of the member record is as it was, and the onboarding
        # endpoint answers with the same shape.
        onboarding = client.post("/api/auth/me/onboarding-complete")
        assert onboarding.status_code == 200
        assert onboarding.json()["username"] == "jdoe"
        assert onboarding.json()["onboarding_completed"] is True
        assert onboarding.json()["preferences"] == {"ui_locale": "zh-Hans"}
    finally:
        cleanup()


# --- the rendered pages -------------------------------------------------------

def _app_page(monkeypatch, *, accept_language=None, preferences_json=None):
    from app.main import app
    import app.web as web_module
    from app.services.auth_service import issue_session_token

    db = _database()
    user = User(
        username="a1234567",
        nickname="Jane Doe",
        password_hash="hash",
        role="user",
        is_active=True,
        preferences_json=preferences_json,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(UserAllowlistEntry(username="a1234567", role="user", is_active=True))
    db.commit()
    monkeypatch.setattr(web_module, "SessionLocal", lambda: db)
    client = TestClient(app)
    client.cookies.set(web_module.settings.session_cookie_name, issue_session_token(user.id))
    headers = {"Accept-Language": accept_language} if accept_language else {}
    try:
        response = client.get("/app", headers=headers, follow_redirects=False)
    finally:
        db.close()
    assert response.status_code == 200, response.status_code
    return response.text


def test_app_page_defaults_to_english(monkeypatch):
    html = _app_page(monkeypatch)
    assert '<html lang="en" data-locale="en" data-reply-language="en" data-reply-language-strict="false">' in html
    assert 'data-locale-source="default"' in html
    assert 'aria-checked="true" data-ui-locale-option="auto"' in html
    # Language is the only language setting: nothing else to choose.
    assert "account-reply-language" not in html
    assert "replies in" not in html


def test_app_page_follows_the_browser_until_the_member_chooses(monkeypatch):
    html = _app_page(monkeypatch, accept_language="zh-HK,zh;q=0.9,en;q=0.8")
    assert '<html lang="zh-Hant-HK" data-locale="zh-Hant-HK" data-reply-language="zh-Hant-HK" data-reply-language-strict="false">' in html
    assert 'data-locale-source="browser"' in html
    # The menu shows the preference (Auto), not what it resolved to.
    assert 'aria-checked="true" data-ui-locale-option="auto"' in html
    assert 'aria-checked="false" data-ui-locale-option="zh-Hant-HK"' in html


def test_app_page_renders_the_saved_preference_over_the_browser(monkeypatch):
    html = _app_page(
        monkeypatch,
        accept_language="zh-HK",
        preferences_json=json.dumps({"ui_locale": "en"}),
    )
    # The chosen language is the assistant's too, strictly.
    assert '<html lang="en" data-locale="en" data-reply-language="en" data-reply-language-strict="true">' in html
    assert 'data-locale-source="preference"' in html
    assert 'aria-checked="true" data-ui-locale-option="en"' in html
    assert 'aria-checked="false" data-ui-locale-option="auto"' in html


def test_pages_without_a_member_follow_the_browser():
    from app.main import app

    client = TestClient(app)
    response = client.get("/login", headers={"Accept-Language": "zh-CN,zh;q=0.9"})
    assert response.status_code == 200
    # No member, so no reply language on the document either.
    assert '<html lang="zh-Hans" data-locale="zh-Hans">' in response.text
    assert '<html lang="en" data-locale="en">' in client.get("/login").text


# --- the chat proxy -----------------------------------------------------------

def test_edit_and_title_requests_are_recognised_as_turns():
    assert _session_turn_request_session_id("POST", "api/sessions/s-1/messages/m-1/edit") == "s-1"
    assert _session_turn_request_session_id("POST", "api/sessions/S-1/messages/m-1/edit/async") == "S-1"
    assert _session_turn_request_session_id("post", "/api/sessions/s-1/title/generate/") == "s-1"
    assert _session_turn_request_session_id("GET", "api/sessions/s-1/title/generate") is None
    assert _session_turn_request_session_id("POST", "api/sessions/s-1/rename") is None
    assert _session_turn_request_session_id("POST", "api/sessions/s-1/messages/m-1/delete-from-here") is None
    assert _session_turn_request_session_id("POST", "api/chat") is None


def test_proxy_fills_the_reply_language_into_edits_and_title_generation(monkeypatch):
    from app.main import app
    import app.api.proxy as proxy_module

    fake_user = SimpleNamespace(
        id=77,
        username="runtime-user",
        nickname="Runtime User",
        role="user",
        preferences_json=json.dumps({"ui_locale": "zh-Hant-HK"}),
    )
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=77, visibility="private", status="running", name="Agent One")

    def _override_user():
        return fake_user

    def _override_db():
        yield object()

    app.dependency_overrides[proxy_module.get_current_user] = _override_user
    app.dependency_overrides[proxy_module.get_db] = _override_db
    captured = []
    try:
        monkeypatch.setattr(proxy_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent))
        monkeypatch.setattr(
            proxy_module.runtime_execution_context_service,
            "build_runtime_metadata",
            lambda _db, _agent: {"runtime_profile_id": "server-runtime", "provider": "openai", "model": "gpt-5"},
        )

        async def _fake_forward(**kwargs):
            captured.append(kwargs)
            return 200, b'{"ok": true}', "application/json"

        monkeypatch.setattr(proxy_module.proxy_service, "forward", _fake_forward)
        client = TestClient(app)
        edit = client.post(
            "/a/agent-1/api/sessions/s-1/messages/m-1/edit/async",
            json={"content": "edited", "request_id": "r-1", "metadata": {"provider": "spoofed"}},
            headers={"Accept-Language": "zh-CN"},
        )
        title = client.post("/a/agent-1/api/sessions/s-1/title/generate", json={"force": True})
        rename = client.post("/a/agent-1/api/sessions/s-1/rename", json={"name": "Renamed"})
    finally:
        app.dependency_overrides.clear()

    assert (edit.status_code, title.status_code, rename.status_code) == (200, 200, 200)
    edit_body = json.loads(captured[0]["body"].decode("utf-8"))
    assert edit_body["content"] == "edited"
    assert edit_body["request_id"] == "r-1"
    assert edit_body["session_id"] == "s-1"
    # The spoofed metadata is gone; the saved language (strict) beats the browser's zh-CN.
    assert edit_body["metadata"]["provider"] == "openai"
    assert edit_body["metadata"]["portal_user"] == {
        "id": "77",
        "username": "runtime-user",
        "display_name": "Runtime User",
        "reply_language": "zh-Hant-HK",
        "reply_language_strict": True,
    }
    title_body = json.loads(captured[1]["body"].decode("utf-8"))
    assert title_body["force"] is True
    assert title_body["metadata"]["portal_user"]["reply_language"] == "zh-Hant-HK"
    # A rename makes no model call: forwarded as it came.
    assert json.loads(captured[2]["body"].decode("utf-8")) == {"name": "Renamed"}


# --- the markup, its wiring and the contract ----------------------------------

def test_the_account_menu_has_the_language_group_and_chat_ui_wires_it():
    start = APP_HTML.index('id="account-menu"')
    menu = APP_HTML[start:APP_HTML.index('id="logout-btn"', start)]
    assert 'aria-labelledby="account-language-label"' in menu
    assert '>{{ t("Language") }}<' in menu
    for choice in ("auto", "en", "zh-Hans", "zh-Hant-HK"):
        assert f'role="menuitemradio" aria-checked="{{{{ \'true\' if ui_locale_preference == \'{choice}\' else \'false\' }}}}" data-ui-locale-option="{choice}"' in menu, choice
    assert 'class="portal-account-options is-four"' in menu
    # One language setting: no reply-language select.
    assert "account-reply-language" not in menu
    assert "<select" not in menu
    # The languages are named in their own script, as entities so the template stays ASCII.
    assert "&#x7B80;&#x4F53;" in menu and "&#x7E41;&#x9AD4;" in menu
    assert menu.isascii()
    assert menu.index('data-chat-width-option="full"') < menu.index('data-ui-locale-option="auto"')
    assert 'uiLocaleOptions: Array.from(document.querySelectorAll("[data-ui-locale-option]"))' in CHAT_JS
    assert "replyLanguageSelect" not in CHAT_JS
    assert "reply_language:" not in CHAT_JS
    assert 'option.addEventListener("click", () => applyUiLocalePreference(option.dataset.uiLocaleOption))' in CHAT_JS
    assert 'fetch("/api/auth/me/preferences", {' in CHAT_JS


def test_the_page_language_is_rendered_by_the_server():
    assert '<html lang="en">' not in BASE_HTML
    assert "current_locale()" in BASE_HTML
    assert 'lang="{{ page_locale }}" data-locale="{{ page_locale }}"' in BASE_HTML
    assert 'html[lang="zh-Hans"] body' in CSS
    assert 'html[lang="zh-Hant-HK"] body' in CSS
    assert '"PingFang HK"' in CSS and '"Microsoft JhengHei"' in CSS
    assert '"PingFang SC"' in CSS and '"Microsoft YaHei"' in CSS
    assert ".portal-account-options.is-four" in CSS


def test_the_contract_documents_the_reply_language():
    assert "`portal_user.reply_language`" in CONTRACT
    assert "reply_language_strict" in CONTRACT
    assert "title/generate" in CONTRACT


def test_reload_decision_and_export_fonts_in_node():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping the language helper test")
    script = "\n".join(
        [
            _extract_js_function(CHAT_JS, "localeChangeNeedsReload"),
            _extract_js_function(CHAT_JS, "exportFontFamily"),
            """
console.log(JSON.stringify({
  changed: localeChangeNeedsReload("zh-Hans", "en"),
  same: localeChangeNeedsReload("en", "en"),
  empty: localeChangeNeedsReload("", "en"),
  missing: localeChangeNeedsReload(undefined, "en"),
  hk: exportFontFamily("zh-Hant-HK"),
  hans: exportFontFamily("zh-Hans"),
  en: exportFontFamily("en"),
}));
""",
        ]
    )
    with tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "language_case.js"
        script_path.write_text(script, encoding="utf-8")
        result = subprocess.run([node_bin, str(script_path)], check=False, capture_output=True, encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip())
    assert out["changed"] is True
    assert out["same"] is False and out["empty"] is False and out["missing"] is False
    assert "'PingFang HK'" in out["hk"] and "'PingFang SC'" not in out["hk"]
    assert "'PingFang SC'" in out["hans"] and "'PingFang HK'" not in out["hans"]
    assert out["en"].endswith("'Segoe UI',sans-serif") and "PingFang" not in out["en"]
