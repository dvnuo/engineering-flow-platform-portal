"""The Portal does not start with the built-in SECRET_KEY, and says what it wants instead."""

import logging
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.security.startup_checks import (
    DEFAULT_SECRET_KEY,
    InsecureSecretKeyError,
    check_secret_key,
    run_startup_checks,
    warn_about_session_cookie,
)


def _settings(**overrides):
    values = {
        "secret_key": DEFAULT_SECRET_KEY,
        "debug": False,
        "allow_insecure_secret_key": False,
        "base_uri": "https://efp.example.com",
        "session_cookie_name": "portal_session",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_the_default_key_constant_is_the_settings_default(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    assert Settings(_env_file=None).secret_key == DEFAULT_SECRET_KEY


@pytest.mark.parametrize("key", [DEFAULT_SECRET_KEY, "", "   "])
def test_startup_is_refused_with_the_default_or_an_empty_key(key):
    with pytest.raises(InsecureSecretKeyError) as exc_info:
        check_secret_key(_settings(secret_key=key))
    message = str(exc_info.value)
    # The message has to be enough for an operator reading a crash-looping pod's log.
    assert "SECRET_KEY" in message
    assert "secrets.token_urlsafe" in message
    assert "ALLOW_INSECURE_SECRET_KEY=true" in message
    assert "efp-portal-secret" in message


def test_a_real_key_starts_quietly(caplog):
    with caplog.at_level(logging.INFO, logger="app.security.startup_checks"):
        check_secret_key(_settings(secret_key="a-long-random-value-from-the-secret-store"))
    assert caplog.records == []


def test_debug_mode_accepts_the_default_key():
    check_secret_key(_settings(debug=True))


def test_the_opt_out_starts_with_a_loud_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="app.security.startup_checks"):
        check_secret_key(_settings(allow_insecure_secret_key=True))
    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    text = record.getMessage()
    assert "INSECURE" in text and "SECRET_KEY" in text and "ALLOW_INSECURE_SECRET_KEY" in text
    assert "forge" in text


def test_the_opt_out_is_off_by_default_and_read_from_the_environment(monkeypatch):
    monkeypatch.delenv("ALLOW_INSECURE_SECRET_KEY", raising=False)
    assert Settings(_env_file=None).allow_insecure_secret_key is False
    monkeypatch.setenv("ALLOW_INSECURE_SECRET_KEY", "true")
    assert Settings(_env_file=None).allow_insecure_secret_key is True


# --- the session cookie warning ------------------------------------------------


def test_no_cookie_warning_when_the_cookie_is_hardened(caplog):
    with caplog.at_level(logging.WARNING, logger="app.security.startup_checks"):
        warn_about_session_cookie(_settings())
    assert caplog.records == []


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"base_uri": ""}, "BASE_URI is not an https:// address"),
        ({"base_uri": "http://efp.example.com"}, "BASE_URI is not an https:// address"),
        ({"session_cookie_name": "team_session"}, "SESSION_COOKIE_NAME is set to 'team_session'"),
    ],
)
def test_cookie_warning_names_the_reason(caplog, overrides, reason):
    with caplog.at_level(logging.WARNING, logger="app.security.startup_checks"):
        warn_about_session_cookie(_settings(**overrides))
    (record,) = caplog.records
    text = record.getMessage()
    assert reason in text
    assert "__Host-" in text and "Secure" in text


def test_run_startup_checks_refuses_before_it_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="app.security.startup_checks"):
        with pytest.raises(InsecureSecretKeyError):
            run_startup_checks(_settings(base_uri=""))
    assert caplog.records == []


# --- wired into the app ----------------------------------------------------------


def test_the_checks_run_before_the_portals_own_startup_work():
    import app.main as app_main

    handlers = list(app_main.app.router.on_startup)
    names = [handler.__name__ for handler in handlers]
    assert "portal_security_startup_checks" in names
    # Ahead of on_startup: no database work and no worker starts with the default key.
    assert names.index("portal_security_startup_checks") < names.index("on_startup")


def test_the_app_startup_handler_refuses_the_default_key(monkeypatch):
    import app.main as app_main

    (handler,) = [h for h in app_main.app.router.on_startup if h.__name__ == "portal_security_startup_checks"]
    monkeypatch.setattr(app_main.settings, "secret_key", DEFAULT_SECRET_KEY)
    monkeypatch.setattr(app_main.settings, "debug", False)
    monkeypatch.setattr(app_main.settings, "allow_insecure_secret_key", False)
    with pytest.raises(InsecureSecretKeyError):
        handler()

    monkeypatch.setattr(app_main.settings, "secret_key", "a-long-random-value-from-the-secret-store")
    handler()


def test_serving_is_refused_end_to_end_with_the_default_key(monkeypatch):
    """Entering the app's lifespan is what uvicorn does at start: it has to fail before on_startup runs."""
    from fastapi.testclient import TestClient

    import app.main as app_main

    ran = []
    monkeypatch.setattr(app_main.settings, "secret_key", DEFAULT_SECRET_KEY)
    monkeypatch.setattr(app_main.settings, "debug", False)
    monkeypatch.setattr(app_main.settings, "allow_insecure_secret_key", False)
    monkeypatch.setattr(app_main, "setup_logging", lambda _level: ran.append("on_startup"))
    with pytest.raises(InsecureSecretKeyError):
        with TestClient(app_main.app):
            pass
    assert ran == []
