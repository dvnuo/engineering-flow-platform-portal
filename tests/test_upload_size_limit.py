"""Upload size cap is settings-driven (EFP_MAX_UPLOAD_MB) and enforced.

The Portal previously hardcoded a 10MB check that was in practice never the
binding limit (ingress + runtime capped at 1MB). This locks the new
configurable guard and its application to both upload proxies.
"""

import pytest
from fastapi import HTTPException

import app.web as web
from app.config import Settings


class _FakeSettings:
    def __init__(self, mb):
        self.max_upload_mb = mb


def test_default_max_upload_mb_is_25():
    assert Settings().max_upload_mb == 25


def test_max_upload_mb_env_override(monkeypatch):
    monkeypatch.setenv("EFP_MAX_UPLOAD_MB", "50")
    assert Settings().max_upload_mb == 50


def test_enforce_accepts_file_at_the_cap(monkeypatch):
    monkeypatch.setattr(web, "get_settings", lambda: _FakeSettings(25))
    # Exactly at the cap must be allowed (no exception).
    web._enforce_upload_size(b"x" * (25 * 1024 * 1024))


def test_enforce_rejects_file_over_cap_with_413(monkeypatch):
    monkeypatch.setattr(web, "get_settings", lambda: _FakeSettings(25))
    with pytest.raises(HTTPException) as excinfo:
        web._enforce_upload_size(b"x" * (25 * 1024 * 1024 + 1))
    assert excinfo.value.status_code == 413
    assert "25MB" in excinfo.value.detail


def test_enforce_message_tracks_configured_cap(monkeypatch):
    monkeypatch.setattr(web, "get_settings", lambda: _FakeSettings(1))
    with pytest.raises(HTTPException) as excinfo:
        web._enforce_upload_size(b"x" * (1 * 1024 * 1024 + 1))
    assert excinfo.value.status_code == 413
    assert "1MB" in excinfo.value.detail
    # And a 1MB file is still accepted at the reduced cap.
    web._enforce_upload_size(b"x" * (1 * 1024 * 1024))


def test_invalid_or_nonpositive_setting_falls_back_to_25(monkeypatch):
    monkeypatch.setattr(web, "get_settings", lambda: _FakeSettings(0))
    assert web._max_upload_bytes() == 25 * 1024 * 1024
    monkeypatch.setattr(web, "get_settings", lambda: _FakeSettings("nope"))
    assert web._max_upload_bytes() == 25 * 1024 * 1024
