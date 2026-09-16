"""ENABLED_RUNTIME_TYPES: which engines a Portal offers for *new* assistants.

Every marker in ALLOWED_RUNTIME_TYPES stays valid, so existing assistants,
task records and proxies keep working on an engine that is no longer offered;
only creating an assistant (or type) on it, or switching onto it, is gated.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.contracts.runtime_type import (
    ALLOWED_RUNTIME_TYPES,
    InvalidRuntimeType,
    RuntimeTypeNotEnabled,
    normalize_enabled_runtime_types,
    pick_enabled_runtime_type,
    require_enabled_runtime_type,
)


# ------------------------------------------------------------------ contract


def test_default_setting_offers_native_only():
    from app.config import Settings

    assert normalize_enabled_runtime_types(Settings().enabled_runtime_types) == ("native",)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("native", ("native",)),
        ("opencode", ("opencode",)),
        ("native,opencode", ("native", "opencode")),
        # Canonical order and de-duplication however the variable is written.
        ("opencode, native, opencode", ("native", "opencode")),
        ("OpenCode ; Native", ("native", "opencode")),
        # Unknown markers are dropped rather than breaking creation.
        ("native,codex", ("native",)),
        ("codex", ("native",)),
        ("", ("native",)),
        (None, ("native",)),
    ],
)
def test_normalize_enabled_runtime_types(raw, expected):
    assert normalize_enabled_runtime_types(raw) == expected
    assert set(expected) <= set(ALLOWED_RUNTIME_TYPES)


def test_pick_enabled_runtime_type_prefers_the_default_when_offered():
    assert pick_enabled_runtime_type("opencode", ("native", "opencode")) == "opencode"
    assert pick_enabled_runtime_type("opencode", ("native",)) == "native"
    assert pick_enabled_runtime_type("", ("opencode",)) == "opencode"
    assert pick_enabled_runtime_type("bogus", ("native", "opencode")) == "native"


def test_require_enabled_runtime_type():
    assert require_enabled_runtime_type("OpenCode", ("native", "opencode")) == "opencode"
    with pytest.raises(RuntimeTypeNotEnabled) as refused:
        require_enabled_runtime_type("opencode", ("native",))
    assert "ENABLED_RUNTIME_TYPES=native" in str(refused.value)
    assert isinstance(refused.value, InvalidRuntimeType)
    with pytest.raises(InvalidRuntimeType):
        require_enabled_runtime_type("bogus", ("native", "opencode"))


# ------------------------------------------------------------ create wizard


def _member_create_form(monkeypatch) -> str:
    import app.web as web
    from app.main import app

    member = SimpleNamespace(id=1, username="member", nickname="Member", role="member")
    monkeypatch.setattr(web, "_authorized_web_user", lambda request: (member, None))
    # Not the `with` form: startup runs a schema guard against a database this
    # test does not migrate.
    response = TestClient(app).get("/app")
    assert response.status_code == 200
    return response.text.split('<form id="create-form"', 1)[1].split("</form>", 1)[0]


def _radio_input(form: str, value: str) -> str:
    return form.split(f'value="{value}"', 1)[1].split("/>", 1)[0]


def test_create_wizard_offers_only_native_by_default(monkeypatch):
    form = _member_create_form(monkeypatch)

    assert 'name="runtime_type"' in form
    assert 'value="native"' in form
    assert "EFP Native Runtime" in form
    assert "checked" in _radio_input(form, "native")
    assert 'value="opencode"' not in form
    assert "OpenCode Runtime" not in form


def test_create_wizard_offers_opencode_once_enabled_and_preselects_the_default(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "enabled_runtime_types", "native,opencode")
    monkeypatch.setattr(get_settings(), "default_runtime_type", "opencode")
    form = _member_create_form(monkeypatch)

    assert "checked" not in _radio_input(form, "native")
    assert "checked" in _radio_input(form, "opencode")
    assert "OpenCode Runtime" in form
    assert "Use the opencode runtime adapter." in form


def test_create_wizard_falls_back_to_an_offered_engine_when_the_default_is_not(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "enabled_runtime_types", "native")
    monkeypatch.setattr(get_settings(), "default_runtime_type", "opencode")
    form = _member_create_form(monkeypatch)

    assert "checked" in _radio_input(form, "native")
    assert 'value="opencode"' not in form
