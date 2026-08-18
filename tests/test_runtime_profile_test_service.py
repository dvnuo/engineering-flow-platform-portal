import base64
import asyncio
from types import SimpleNamespace

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


def _ai_settings(**overrides):
    values = {
        "ai_platform_chat_host": "https://chat.int",
        "ai_platform_chat_uri": "/v1/api/v1/chat/completions",
        "ai_platform_ib2b_host": "https://ib2b.int",
        "ai_platform_ib2b_uri": "/dsp/token",
        "ai_platform_trust_token_header": "X-Trust",
        "ai_platform_tracking_prefix": "EFP",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _ai_cfg():
    return {
        "llm": {
            "provider": "ai_platform",
            "model": "gpt-5.4",
            "ai_platform": {
                # These profile values must not override deployment settings.
                "chat": {"host": "https://profile-chat.invalid"},
                "ib2b": {"host": "https://profile-ib2b.invalid"},
                "auth": {
                    "username": "u",
                    "password": "pw",
                    "usercase": "uc",
                    "trust_token_header": "X-Profile-Trust",
                    "tracking_prefix": "PROFILE",
                },
            },
        }
    }


def test_ai_platform_smoke_exchanges_then_calls_chat(monkeypatch):
    svc = RuntimeProfileTestService(_ai_settings())
    calls = []

    async def _fake_http_json_request(*, method, url, headers, payload, timeout):
        calls.append({"url": url, "headers": headers, "payload": payload})
        if "ib2b" in url:
            return True, "ok", {"issued_token": "JWT-123"}
        return True, "ok", {"choices": [{"message": {"content": "pong"}}]}

    monkeypatch.setattr(svc, "_http_json_request", _fake_http_json_request)
    ok, _msg = asyncio.run(svc._test_llm(_ai_cfg()))
    assert ok is True
    assert len(calls) == 2  # iB2B exchange, then chat
    assert calls[0]["url"] == "https://ib2b.int/dsp/token"
    assert calls[0]["payload"]["input_token_state"]["token_type"] == "CREDENTIAL"
    assert calls[1]["url"] == "https://chat.int/v1/api/v1/chat/completions"
    assert calls[1]["headers"]["X-Trust"] == "JWT-123"  # exchanged JWT in the configured header
    assert calls[1]["headers"]["x-correlation-id"].startswith("EFP-")
    assert calls[1]["payload"]["user"] == "uc"


def test_ai_platform_smoke_requires_configured_chat_host():
    svc = RuntimeProfileTestService(_ai_settings(ai_platform_chat_host=""))
    ok, msg = asyncio.run(svc._test_llm(_ai_cfg()))
    assert ok is False
    assert "chat host" in msg.lower()


def test_ai_platform_smoke_requires_all_profile_credentials():
    svc = RuntimeProfileTestService(_ai_settings())
    config = _ai_cfg()
    config["llm"]["ai_platform"]["auth"].pop("usercase")
    ok, msg = asyncio.run(svc._test_llm(config))
    assert ok is False
    assert "username, password, and usercase" in msg.lower()


def test_ai_platform_smoke_reports_exchange_failure(monkeypatch):
    svc = RuntimeProfileTestService(_ai_settings())

    async def _fake_http_json_request(*, method, url, headers, payload, timeout):
        return False, "401 unauthorized", None

    monkeypatch.setattr(svc, "_http_json_request", _fake_http_json_request)
    ok, msg = asyncio.run(svc._test_llm(_ai_cfg()))
    assert ok is False
    assert "token exchange failed" in msg.lower()


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
