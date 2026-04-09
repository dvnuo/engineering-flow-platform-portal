import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from types import SimpleNamespace

from fastapi.testclient import TestClient


def _fake_user(role: str = "user"):
    return SimpleNamespace(
        id=123,
        username="phasea",
        nickname="Phase A",
        role=role,
        is_active=True,
    )


def test_app_shell_phase_a_copy_and_header(monkeypatch):
    import app.web as web
    from app.main import app

    monkeypatch.setattr(web, "_current_user_from_cookie", lambda _request: _fake_user("user"))

    client = TestClient(app)
    response = client.get("/app")

    assert response.status_code == 200
    html = response.text

    assert "Assistants" in html
    assert "Select an assistant" in html
    assert 'placeholder="Message assistant"' in html
    assert 'id="btn-more"' in html

    assert "Active Agent" not in html
    assert 'placeholder="Type message, / for skills"' not in html

    assert 'id="btn-sessions"' not in html
    assert 'id="btn-thinking"' not in html
    assert 'id="btn-files"' not in html
    assert 'id="top-settings"' not in html


def test_admin_still_sees_users_button(monkeypatch):
    import app.web as web
    from app.main import app

    monkeypatch.setattr(web, "_current_user_from_cookie", lambda _request: _fake_user("admin"))

    client = TestClient(app)
    response = client.get("/app")

    assert response.status_code == 200
    assert 'id="users-menu-btn"' in response.text
