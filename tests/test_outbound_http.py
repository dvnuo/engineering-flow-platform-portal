"""Explicit egress settings for the portal's own calls to GitHub and the IdP.

Inside the cluster github.com is usually only reachable through an egress
proxy while the IdP is reachable directly; relying on HTTP(S)_PROXY alone
made failures impossible to tell apart from the pod. Each destination has
its own setting, and failures name the path that was used.
"""

import asyncio

from app.services import outbound_http


def test_empty_setting_keeps_httpx_environment_defaults():
    kwargs = outbound_http.outbound_client_kwargs("", timeout=30.0)
    assert kwargs == {"timeout": 30.0}


def test_direct_setting_ignores_environment_proxies():
    kwargs = outbound_http.outbound_client_kwargs("direct", timeout=30.0)
    assert kwargs == {"timeout": 30.0, "trust_env": False}


def test_explicit_proxy_url_is_passed_to_httpx():
    kwargs = outbound_http.outbound_client_kwargs(" http://proxy.corp:3128 ", timeout=30.0, verify=False)
    assert kwargs == {"timeout": 30.0, "verify": False, "proxy": "http://proxy.corp:3128"}


def test_describe_egress_names_the_path_used(monkeypatch):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    assert outbound_http.describe_egress("") == "direct connection (no proxy configured)"
    monkeypatch.setenv("HTTPS_PROXY", "http://egress.corp:3128")
    assert outbound_http.describe_egress("") == "environment proxy HTTPS_PROXY=http://egress.corp:3128"
    assert outbound_http.describe_egress("direct") == "direct connection (environment proxy settings ignored)"
    assert outbound_http.describe_egress("http://other:8080") == "configured proxy http://other:8080"


def test_github_settings_drive_copilot_and_user_lookup_clients(monkeypatch):
    import app.services.copilot_auth_service as svc_module
    from app.services import external_login_service

    monkeypatch.setattr(outbound_http.settings, "github_proxy_url", "http://egress.corp:3128")
    monkeypatch.setattr(outbound_http.settings, "github_http_timeout_seconds", 45)
    seen = []

    class _Resp:
        status_code = 200
        content = b"{}"
        text = "{}"

        def json(self):
            return {"login": "alice", "name": "Alice", "email": ""}

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *args, **kwargs):
            seen.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            return _Resp()

        async def get(self, *args, **kwargs):
            return _Resp()

    monkeypatch.setattr(svc_module.httpx, "AsyncClient", _Client)
    asyncio.run(svc_module.CopilotAuthService().start_authorization("u", None))
    asyncio.run(external_login_service.fetch_github_user("tok"))
    assert seen and all(k.get("proxy") == "http://egress.corp:3128" and k.get("timeout") == 45.0 for k in seen)


def test_copilot_start_failure_reports_the_egress_path(monkeypatch):
    import app.services.copilot_auth_service as svc_module

    monkeypatch.setattr(outbound_http.settings, "github_proxy_url", "http://egress.corp:3128")

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            raise svc_module.httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(svc_module.httpx, "AsyncClient", _Client)
    status, payload = asyncio.run(svc_module.CopilotAuthService().start_authorization("u", None))
    assert status == 500
    assert "configured proxy http://egress.corp:3128" in payload["details"]
