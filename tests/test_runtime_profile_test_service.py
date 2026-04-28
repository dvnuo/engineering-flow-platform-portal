import base64
import asyncio

from app.services.runtime_profile_test_service import RuntimeProfileTestService


def test_build_auth_prefers_basic_for_username_token():
    headers = RuntimeProfileTestService._build_auth({"username": "u@example.com", "token": "tok-123"})
    assert headers["Authorization"].startswith("Basic ")
    encoded = headers["Authorization"].split(" ", 1)[1]
    assert base64.b64decode(encoded).decode("utf-8") == "u@example.com:tok-123"


def test_build_auth_uses_bearer_for_token_only():
    headers = RuntimeProfileTestService._build_auth({"token": "tok-123"})
    assert headers == {"Authorization": "Bearer tok-123"}


def test_build_auth_uses_basic_for_username_password():
    headers = RuntimeProfileTestService._build_auth({"username": "u@example.com", "password": "pw-123"})
    assert headers["Authorization"].startswith("Basic ")
    encoded = headers["Authorization"].split(" ", 1)[1]
    assert base64.b64decode(encoded).decode("utf-8") == "u@example.com:pw-123"


def test_first_auth_instance_accepts_username_token():
    svc = RuntimeProfileTestService()
    picked = svc._first_auth_instance([
        {"url": "https://a.atlassian.net", "username": "u@example.com", "token": "tok"},
        {"url": "https://b.atlassian.net", "token": "tok2"},
    ])
    assert picked is not None
    assert picked["url"] == "https://a.atlassian.net"


def test_jira_uses_basic_for_username_token(monkeypatch):
    svc = RuntimeProfileTestService()
    seen = {}

    async def _fake_http_json_request(*, method, url, headers, payload, timeout):
        seen["headers"] = headers
        return True, "ok", {"displayName": "User"}

    monkeypatch.setattr(svc, "_http_json_request", _fake_http_json_request)
    ok, _msg = asyncio.run(
        svc._test_jira(
            {
                "jira": {
                    "enabled": True,
                    "instances": [{"url": "https://a.atlassian.net", "username": "u@example.com", "token": "tok"}],
                }
            }
        )
    )
    assert ok is True
    assert seen["headers"]["Authorization"].startswith("Basic ")


def test_confluence_uses_basic_for_username_token(monkeypatch):
    svc = RuntimeProfileTestService()
    seen = {}

    async def _fake_http_json_request(*, method, url, headers, payload, timeout):
        seen["headers"] = headers
        return True, "ok", {"results": []}

    monkeypatch.setattr(svc, "_http_json_request", _fake_http_json_request)
    ok, _msg = asyncio.run(
        svc._test_confluence(
            {
                "confluence": {
                    "enabled": True,
                    "instances": [{"url": "https://a.atlassian.net/wiki", "username": "u@example.com", "token": "tok"}],
                }
            }
        )
    )
    assert ok is True
    assert seen["headers"]["Authorization"].startswith("Basic ")


import pytest


def test_llm_smoke_openai_gpt4_includes_temperature(monkeypatch):
    svc = RuntimeProfileTestService()
    captured = {}

    async def fake_provider_request(provider, model, endpoint, headers, payload):
        captured["payload"] = payload
        return True, "ok"

    monkeypatch.setattr(svc, "_provider_request", fake_provider_request)

    ok, _ = asyncio.run(svc._test_llm({"llm": {"provider": "openai", "model": "gpt-4", "api_key": "k"}}))
    assert ok is True
    assert captured["payload"]["temperature"] == 0


@pytest.mark.parametrize("model", ["gpt-4.1", "gpt-4o", "gpt-5.4-mini"])
def test_llm_smoke_openai_non_exact_gpt4_omits_temperature(monkeypatch, model):
    svc = RuntimeProfileTestService()
    captured = {}

    async def fake_provider_request(provider, model_arg, endpoint, headers, payload):
        captured["payload"] = payload
        return True, "ok"

    monkeypatch.setattr(svc, "_provider_request", fake_provider_request)

    ok, _ = asyncio.run(svc._test_llm({"llm": {"provider": "openai", "model": model, "api_key": "k"}}))
    assert ok is True
    assert "temperature" not in captured["payload"]


def test_llm_smoke_anthropic_omits_temperature(monkeypatch):
    svc = RuntimeProfileTestService()
    captured = {}

    async def fake_provider_request(provider, model, endpoint, headers, payload):
        captured["payload"] = payload
        return True, "ok"

    monkeypatch.setattr(svc, "_provider_request", fake_provider_request)

    ok, _ = asyncio.run(svc._test_llm({"llm": {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "api_key": "k"}}))
    assert ok is True
    assert "temperature" not in captured["payload"]
