import base64
import asyncio
from types import SimpleNamespace

import pytest

from app.services.runtime_profile_test_service import RuntimeProfileTestService


def _basic_pair(headers: dict) -> str:
    scheme, encoded = headers["Authorization"].split(" ", 1)
    assert scheme == "Basic"
    return base64.b64decode(encoded).decode("utf-8")


@pytest.mark.parametrize(
    "url",
    [
        "https://acme.atlassian.net",
        "https://acme.atlassian.net/wiki",
        "https://ACME.Atlassian.NET/wiki/",
        "https://acme.jira.com",
        "https://api.atlassian.com/ex/confluence/0f1e2d3c",
    ],
)
def test_build_auth_on_atlassian_cloud_signs_in_with_email_and_api_token(url):
    row = {"url": url, "username": "u@example.com", "token": "tok-123"}
    assert _basic_pair(RuntimeProfileTestService._build_auth(row)) == "u@example.com:tok-123"


@pytest.mark.parametrize(
    "url",
    [
        "https://wiki.corp.example",
        "https://jira.corp.example:8443/jira",
        "wiki.corp.example",
        # A company's own host is not Atlassian's because it ends alike.
        "https://notatlassian.net",
        "https://acme.atlassian.net.corp.example",
        "",
    ],
)
def test_build_auth_on_a_company_hosted_site_sends_the_token_alone(url):
    # A Data Center personal access token is accepted as Bearer only: sent as
    # Basic beside the username the site answers 401.
    row = {"url": url, "username": "u@example.com", "token": "tok-123"}
    assert RuntimeProfileTestService._build_auth(row) == {"Authorization": "Bearer tok-123"}


def test_build_auth_uses_bearer_for_token_only():
    headers = RuntimeProfileTestService._build_auth({"token": "tok-123"})
    assert headers == {"Authorization": "Bearer tok-123"}
    cloud = RuntimeProfileTestService._build_auth({"url": "https://acme.atlassian.net", "token": "tok-123"})
    assert cloud == {"Authorization": "Bearer tok-123"}


def test_build_auth_uses_basic_for_username_password():
    headers = RuntimeProfileTestService._build_auth({"username": "u@example.com", "password": "pw-123"})
    assert _basic_pair(headers) == "u@example.com:pw-123"


def test_build_auth_lets_a_token_win_over_a_saved_password():
    # A row that still holds the password from before the member added a
    # token: the token is what signs in, on either kind of site, the same as
    # the runtimes project it to the CLIs.
    hosted = {"url": "https://wiki.corp.example", "username": "u", "password": "pw-123", "token": "tok-123"}
    assert RuntimeProfileTestService._build_auth(hosted) == {"Authorization": "Bearer tok-123"}
    cloud = {"url": "https://acme.atlassian.net", "username": "u@example.com", "password": "pw-123", "token": "tok-123"}
    assert _basic_pair(RuntimeProfileTestService._build_auth(cloud)) == "u@example.com:tok-123"


def test_a_delegation_rule_signs_in_to_jira_like_the_test_button():
    from app.services.provider_config_resolver import _auth_headers_for_instance

    for row in (
        {"url": "https://jira.corp.example", "username": "u", "token": "tok-123"},
        {"url": "https://acme.atlassian.net", "username": "u@example.com", "token": "tok-123"},
        {"url": "https://jira.corp.example", "username": "u", "password": "pw-123"},
        {"url": "https://jira.corp.example", "token": "tok-123"},
    ):
        assert _auth_headers_for_instance(row) == RuntimeProfileTestService._build_auth(row)


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

    async def _fake_http_json_request(*, method, url, headers, payload, timeout, client_kwargs=None):
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

    async def _fake_http_json_request(*, method, url, headers, payload, timeout, client_kwargs=None):
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

    async def _fake_http_json_request(*, method, url, headers, payload, timeout, client_kwargs=None):
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

    async def _fake_http_json_request(*, method, url, headers, payload, timeout, client_kwargs=None):
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


@pytest.mark.parametrize(
    ("section", "url"),
    [("jira", "https://jira.corp.example"), ("confluence", "https://wiki.corp.example/confluence")],
)
def test_a_company_hosted_site_is_tested_with_the_token_as_bearer(monkeypatch, section, url):
    svc = RuntimeProfileTestService()
    seen = {}

    async def _fake_http_json_request(*, method, url, headers, payload, timeout, client_kwargs=None):
        seen["headers"] = headers
        return True, "ok", {"displayName": "User", "results": []}

    monkeypatch.setattr(svc, "_http_json_request", _fake_http_json_request)
    config = {section: {"enabled": True, "instances": [{"url": url, "username": "u", "token": "tok"}]}}
    ok, _msg = asyncio.run(getattr(svc, f"_test_{section}")(config))
    assert ok is True
    assert seen["headers"]["Authorization"] == "Bearer tok"


def _responses_settings():
    return _ai_settings(ai_platform_responses_uri="/v1/{usercase}/responses")


def _record_calls(svc, monkeypatch):
    calls = []

    async def _fake_http_json_request(*, method, url, headers, payload, timeout, client_kwargs=None):
        calls.append({"url": url, "headers": headers, "payload": payload})
        if "ib2b" in url:
            return True, "ok", {"issued_token": "JWT-123"}
        return True, "ok", {"id": "resp_1", "output": []}

    monkeypatch.setattr(svc, "_http_json_request", _fake_http_json_request)
    return calls


def test_ai_platform_smoke_uses_responses_endpoint_for_native(monkeypatch):
    svc = RuntimeProfileTestService(_responses_settings())
    calls = _record_calls(svc, monkeypatch)

    ok, msg = asyncio.run(svc._test_llm(_ai_cfg(), runtime_type="native"))

    assert ok is True, msg
    assert calls[1]["url"] == "https://chat.int/v1/uc/responses"
    # Responses wants the JWT both in the trust header and as a Bearer token,
    # and takes a plain ``input`` instead of chat messages / ``user``.
    assert calls[1]["headers"]["X-Trust"] == "JWT-123"
    assert calls[1]["headers"]["Authorization"] == "Bearer JWT-123"
    assert calls[1]["payload"] == {"model": "gpt-5.4", "input": "ping"}


def test_ai_platform_smoke_defaults_to_native_routing(monkeypatch):
    svc = RuntimeProfileTestService(_responses_settings())
    calls = _record_calls(svc, monkeypatch)

    ok, _msg = asyncio.run(svc._test_llm(_ai_cfg()))

    assert ok is True
    assert calls[1]["url"] == "https://chat.int/v1/uc/responses"


def test_ai_platform_smoke_keeps_chat_endpoint_for_opencode(monkeypatch):
    svc = RuntimeProfileTestService(_responses_settings())
    calls = _record_calls(svc, monkeypatch)

    ok, _msg = asyncio.run(svc._test_llm(_ai_cfg(), runtime_type="opencode"))

    assert ok is True
    assert calls[1]["url"] == "https://chat.int/v1/api/v1/chat/completions"
    assert "Authorization" not in calls[1]["headers"]
    assert calls[1]["payload"]["messages"] == [{"role": "user", "content": "ping"}]
    assert calls[1]["payload"]["user"] == "uc"


def test_ai_platform_smoke_stays_on_chat_without_responses_path(monkeypatch):
    svc = RuntimeProfileTestService(_ai_settings())
    calls = _record_calls(svc, monkeypatch)

    ok, _msg = asyncio.run(svc._test_llm(_ai_cfg(), runtime_type="native"))

    assert ok is True
    assert calls[1]["url"] == "https://chat.int/v1/api/v1/chat/completions"
    assert "Authorization" not in calls[1]["headers"]
