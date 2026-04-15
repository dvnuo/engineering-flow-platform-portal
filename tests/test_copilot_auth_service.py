from __future__ import annotations

from datetime import timedelta

import asyncio

import pytest

import app.services.copilot_auth_service as svc_module
from app.services.copilot_auth_service import CopilotAuthService
from app.utils.github_url import normalize_github_api_base_url


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.content = b"1"

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, recorder, response_factory):
        self._recorder = recorder
        self._response_factory = response_factory

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self._recorder.append({"url": url, "headers": headers, "json": json})
        return self._response_factory(url, headers, json)


@pytest.fixture(autouse=True)
def _reset_pending():
    svc_module._pending_authorizations.clear()
    yield
    svc_module._pending_authorizations.clear()


def test_normalize_github_api_base_url_cases():
    assert normalize_github_api_base_url("") == "https://api.github.com"
    assert normalize_github_api_base_url(None) == "https://api.github.com"
    assert normalize_github_api_base_url("https://github.com") == "https://api.github.com"
    assert normalize_github_api_base_url("https://api.github.com/") == "https://api.github.com"
    assert normalize_github_api_base_url("https://github.company.com") == "https://github.company.com/api/v3"
    assert normalize_github_api_base_url("https://github.company.com/api/v3/") == "https://github.company.com/api/v3"
    assert normalize_github_api_base_url("github.company.com:8443") == "https://github.company.com:8443/api/v3"
    assert normalize_github_api_base_url("http://github.company.com") == "https://github.company.com/api/v3"
    assert normalize_github_api_base_url("https://github.com:443") == "https://api.github.com"
    assert normalize_github_api_base_url("https://api.github.com:443") == "https://api.github.com"
    assert normalize_github_api_base_url("www.github.com") == "https://www.github.com/api/v3"
    assert normalize_github_api_base_url("https://WWW.GITHUB.COM") == "https://www.github.com/api/v3"


def test_start_uses_normalized_public_base(monkeypatch):
    calls = []

    def _factory(_url, _headers, _json):
        return _FakeResponse(201, {
            "device_code": "d1",
            "user_code": "u1",
            "verification_uri": "https://github.com/login/device",
            "verification_uri_complete": "https://github.com/login/device?x=1",
            "expires_in": 900,
            "interval": 5,
        })

    monkeypatch.setattr(svc_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(calls, _factory))
    service = CopilotAuthService()

    status, payload = asyncio.run(service.start_authorization("u", ""))
    assert status == 200
    assert calls[0]["url"] == "https://api.github.com/copilot/token_verification"
    assert payload["device_code"] == "d1"


def test_start_uses_normalized_enterprise_base(monkeypatch):
    calls = []

    def _factory(_url, _headers, _json):
        return _FakeResponse(201, {
            "device_code": "d2",
            "user_code": "u2",
            "verification_uri": "https://ghe/login/device",
            "verification_uri_complete": "https://ghe/login/device?x=2",
            "expires_in": 900,
            "interval": 7,
        })

    monkeypatch.setattr(svc_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(calls, _factory))
    service = CopilotAuthService()

    status, _payload = asyncio.run(service.start_authorization("u", "https://github.company.com"))
    assert status == 200
    assert calls[0]["url"] == "https://github.company.com/api/v3/copilot/token_verification"


def test_check_pending_authorized_declined_and_expired(monkeypatch):
    calls = []
    mode = {"state": "pending"}

    def _factory(_url, _headers, payload):
        if payload.get("action") == "create":
            return _FakeResponse(201, {
                "device_code": "d1",
                "user_code": "u1",
                "verification_uri": "https://github.com/login/device",
                "verification_uri_complete": "https://github.com/login/device?x=1",
                "expires_in": 900,
                "interval": 5,
            })
        if mode["state"] == "pending":
            return _FakeResponse(400, {"error": "authorization_pending"})
        if mode["state"] == "authorized":
            return _FakeResponse(200, {"token": "copilot-token"})
        if mode["state"] == "declined":
            return _FakeResponse(400, {"error": "authorization_declined"})
        return _FakeResponse(500, {"error": "server_error"})

    monkeypatch.setattr(svc_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(calls, _factory))
    service = CopilotAuthService()

    # pending
    status, start_payload = asyncio.run(service.start_authorization("user-a", ""))
    assert status == 200
    status, payload = asyncio.run(service.check_authorization("user-a", start_payload["auth_id"], start_payload["device_code"]))
    assert status == 200
    assert payload["status"] == "pending"

    # authorized and record cleanup
    mode["state"] = "authorized"
    status, payload = asyncio.run(service.check_authorization("user-a", start_payload["auth_id"], start_payload["device_code"]))
    assert status == 200
    assert payload["status"] == "authorized"
    assert payload["token"] == "copilot-token"
    assert start_payload["auth_id"] not in svc_module._pending_authorizations

    # declined
    mode["state"] = "declined"
    _, start_payload2 = asyncio.run(service.start_authorization("user-a", ""))
    status, payload = asyncio.run(service.check_authorization("user-a", start_payload2["auth_id"], start_payload2["device_code"]))
    assert status == 200
    assert payload["status"] == "declined"

    # expired
    _, start_payload3 = asyncio.run(service.start_authorization("user-a", ""))
    svc_module._pending_authorizations[start_payload3["auth_id"]]["expires_at"] = service._utc_now() - timedelta(seconds=1)
    status, payload = asyncio.run(service.check_authorization("user-a", start_payload3["auth_id"], start_payload3["device_code"]))
    assert status == 200
    assert payload["status"] == "expired"


def test_auth_session_is_user_bound(monkeypatch):
    calls = []

    def _factory(_url, _headers, _json):
        return _FakeResponse(201, {
            "device_code": "d1",
            "user_code": "u1",
            "verification_uri": "https://github.com/login/device",
            "verification_uri_complete": "https://github.com/login/device?x=1",
            "expires_in": 900,
            "interval": 5,
        })

    monkeypatch.setattr(svc_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(calls, _factory))
    service = CopilotAuthService()

    _, payload = asyncio.run(service.start_authorization("user-a", ""))
    status, check_payload = asyncio.run(service.check_authorization("user-b", payload["auth_id"], payload["device_code"]))
    assert status == 404
    assert check_payload["error"] == "Authorization not found or expired"


def test_start_fails_when_github_payload_missing_device_code(monkeypatch):
    calls = []

    def _factory(_url, _headers, _json):
        return _FakeResponse(201, {
            "user_code": "u1",
            "verification_uri": "https://github.com/login/device",
            "verification_uri_complete": "https://github.com/login/device?x=1",
            "expires_in": 900,
            "interval": 5,
        })

    monkeypatch.setattr(svc_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(calls, _factory))
    service = CopilotAuthService()

    status, payload = asyncio.run(service.start_authorization("u", ""))
    assert status == 502
    assert payload["error"] == "GitHub authorization start failed"
    assert "device_code" in payload["details"]
    assert len(svc_module._pending_authorizations) == 0


def test_start_uses_normalized_public_base_for_github_dot_com_with_port(monkeypatch):
    calls = []

    def _factory(_url, _headers, _json):
        return _FakeResponse(201, {
            "device_code": "d-port-1",
            "user_code": "u-port-1",
            "verification_uri": "https://github.com/login/device",
            "verification_uri_complete": "https://github.com/login/device?x=port1",
            "expires_in": 900,
            "interval": 5,
        })

    monkeypatch.setattr(svc_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(calls, _factory))
    service = CopilotAuthService()

    status, _payload = asyncio.run(service.start_authorization("u", "https://github.com:443"))
    assert status == 200
    assert calls[0]["url"] == "https://api.github.com/copilot/token_verification"


def test_start_uses_normalized_public_base_for_api_github_with_port(monkeypatch):
    calls = []

    def _factory(_url, _headers, _json):
        return _FakeResponse(201, {
            "device_code": "d-port-2",
            "user_code": "u-port-2",
            "verification_uri": "https://github.com/login/device",
            "verification_uri_complete": "https://github.com/login/device?x=port2",
            "expires_in": 900,
            "interval": 5,
        })

    monkeypatch.setattr(svc_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(calls, _factory))
    service = CopilotAuthService()

    status, _payload = asyncio.run(service.start_authorization("u", "https://api.github.com:443"))
    assert status == 200
    assert calls[0]["url"] == "https://api.github.com/copilot/token_verification"
