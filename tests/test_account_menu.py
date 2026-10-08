"""The account menu at the bottom of the rail.

The rail's bottom used to be four identical tiles: Connectors, Help, Theme and
Logout. Only Connectors is a section; the rest are one-off actions, so they
moved behind the member's avatar, which also shows who is signed in. The
avatar is the company photo directory's picture when AVATAR_PHOTO_HOST is set
and the member's initials otherwise (or when the photo request fails).
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.models import User, UserAllowlistEntry
from app.services.member_avatar import avatar_initials, avatar_photo_url

REPO = Path(__file__).resolve().parents[1]
APP_HTML = (REPO / "app" / "templates" / "app.html").read_text(encoding="utf-8")
CHAT_JS = (REPO / "app" / "static" / "js" / "chat_ui.js").read_text(encoding="utf-8")
TOOLTIPS_JS = (REPO / "app" / "static" / "js" / "tooltips.js").read_text(encoding="utf-8")
STARTUP_JS = (REPO / "app" / "static" / "js" / "assistant_startup.js").read_text(encoding="utf-8")

PHOTO_HOST = "https://photos.example.com"


# --- the photo URL and the initials -----------------------------------------

@pytest.mark.parametrize(
    ("host", "username", "expected"),
    [
        (PHOTO_HOST, "a1234567", f"{PHOTO_HOST}/casual/square/a123/a1234567.jpg"),
        # The scheme is optional and a trailing slash is tolerated.
        ("photos.example.com/", "jdoe-eng", f"{PHOTO_HOST}/casual/square/jdoe/jdoe-eng.jpg"),
        # A short username is its own prefix.
        (PHOTO_HOST, "bob", f"{PHOTO_HOST}/casual/square/bob/bob.jpg"),
        # Anything the directory would not expect in a path segment is escaped.
        (PHOTO_HOST, "o'neil/x", f"{PHOTO_HOST}/casual/square/o%27ne/o%27neil%2Fx.jpg"),
    ],
)
def test_avatar_photo_url_follows_the_directory_layout(host, username, expected):
    assert avatar_photo_url(username, host) == expected


@pytest.mark.parametrize(
    ("host", "username"),
    [("", "jdoe"), (None, "jdoe"), ("  ", "jdoe"), (PHOTO_HOST, ""), (PHOTO_HOST, None)],
)
def test_avatar_photo_url_is_empty_without_host_or_username(host, username):
    assert avatar_photo_url(username, host) == ""


@pytest.mark.parametrize(
    ("display_name", "username", "expected"),
    [
        ("Jane Doe", "jdoe", "JD"),
        ("Jane van der Berg", "jvdb", "JB"),
        ("jane.doe", "x", "JD"),
        ("Jane", "x", "JA"),
        # A two-character CJK name is shown whole.
        ("肖杰", "x", "肖杰"),
        # The username is the last resort.
        ("", "a1234567", "A1"),
        ("   ", None, "?"),
    ],
)
def test_avatar_initials(display_name, username, expected):
    assert avatar_initials(display_name, username) == expected


def test_avatar_photo_host_setting_defaults_to_off(monkeypatch):
    monkeypatch.delenv("AVATAR_PHOTO_HOST", raising=False)
    assert Settings().avatar_photo_host == ""
    monkeypatch.setenv("AVATAR_PHOTO_HOST", PHOTO_HOST)
    assert Settings().avatar_photo_host == PHOTO_HOST


# --- the rendered page --------------------------------------------------------

def _database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)()


def _app_page(monkeypatch, *, username, nickname, role="user", photo_host=""):
    from app.main import app
    import app.web as web_module
    from app.services.auth_service import issue_session_token

    db = _database()
    user = User(username=username, nickname=nickname, password_hash="hash", role=role, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(UserAllowlistEntry(username=username, role=role, is_active=True))
    db.commit()
    monkeypatch.setattr(web_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(web_module.get_settings(), "avatar_photo_host", photo_host)
    client = TestClient(app)
    client.cookies.set(web_module.settings.session_cookie_name, issue_session_token(user.id))
    try:
        response = client.get("/app", follow_redirects=False)
    finally:
        db.close()
    assert response.status_code == 200, response.status_code
    return response.text


def test_app_page_shows_the_directory_photo_when_the_host_is_configured(monkeypatch):
    html = _app_page(monkeypatch, username="a1234567", nickname="Jane Doe", photo_host=PHOTO_HOST)
    photo = f'src="{PHOTO_HOST}/casual/square/a123/a1234567.jpg"'
    # Once on the rail button, once in the menu's identity row; the initials
    # sit underneath both so a missing picture degrades to them.
    assert html.count(photo) == 2
    assert html.count('class="portal-avatar-initials">JD<') == 2
    assert 'aria-label="Account: Jane Doe"' in html
    assert '<div class="portal-account-name">Jane Doe</div>' in html
    assert '<div class="portal-account-meta">Member &middot; a1234567</div>' in html


def test_app_page_falls_back_to_initials_without_a_photo_host(monkeypatch):
    html = _app_page(monkeypatch, username="admin", nickname=None, role="admin")
    assert "portal-avatar-photo" not in html
    assert html.count('class="portal-avatar-initials">AD<') == 2
    # Nickname and username coincide, so the username is not repeated.
    assert '<div class="portal-account-meta">Administrator</div>' in html


# --- the markup and its wiring ----------------------------------------------

def test_rail_bottom_is_connectors_plus_the_account_button():
    start = APP_HTML.index('<div class="portal-rail-bottom">')
    rail_bottom = APP_HTML[start:APP_HTML.index("</nav>", start)]
    assert rail_bottom.count('class="portal-rail-btn') == 2
    assert 'id="connectors-menu-btn"' in rail_bottom
    for marker in (
        'id="account-menu-btn"',
        'aria-haspopup="menu"',
        'aria-controls="account-menu"',
        'id="account-menu"',
        'role="menu"',
        'role="menuitemradio"',
        'data-theme-option="system"',
        'data-theme-option="light"',
        'data-theme-option="dark"',
        'id="help-btn"',
        'id="shortcuts-btn"',
        'id="logout-btn"',
    ):
        assert marker in rail_bottom, marker
    # The theme is a choice now, not a cycle button.
    assert "theme-toggle" not in APP_HTML
    assert "toggleTheme" not in CHAT_JS
    assert "THEME_META" not in CHAT_JS


def test_chat_ui_wires_the_account_menu():
    assert "function setAccountMenuOpen(" in CHAT_JS
    assert 'option.addEventListener("click", () => applyTheme(option.dataset.themeOption))' in CHAT_JS
    assert 'option.setAttribute("aria-checked", option.dataset.themeOption === preference ? "true" : "false");' in CHAT_JS
    assert 'openHelpTopic("shortcuts")' in CHAT_JS
    # Escape is handled in the capture phase so the run-abort and drawer
    # handlers never see a keypress meant for the menu.
    assert 'if (event.key !== "Escape" || !isAccountMenuOpen()) return;' in CHAT_JS
    # Focus leaving the button and menu (Tab out, or a shortcut moving focus
    # to the composer) closes the menu instead of leaving it open.
    assert 'dom.accountMenu?.parentElement?.addEventListener("focusout"' in CHAT_JS
    assert 'photo.addEventListener("error", showInitials);' in CHAT_JS
    # Help is still the section the startup card's "contact support" reaches.
    assert 'document.getElementById("help-btn")?.click();' in STARTUP_JS


def test_tooltips_describe_the_account_button_not_its_menu_items():
    assert '["#account-menu-btn", ' in TOOLTIPS_JS
    assert '["#help-btn", ' not in TOOLTIPS_JS
    assert '["#logout-btn", ' not in TOOLTIPS_JS
