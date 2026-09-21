"""Test-connection checks for jenkins, nexus, splunk and pgsql.

Each check runs against the section's default instance (or the first usable
one) with the credentials the profile holds, through a mocked httpx transport
so the exact request -- path, auth header -- is what is asserted.
PostgreSQL is reachability only: the Portal rarely sees the database, and the
sign-in is verified inside the runtime by `pgsql auth test`.
"""
import asyncio
import base64

import httpx
import pytest

import app.services.runtime_profile_test_service as test_service_module
from app.services.runtime_profile_test_service import RuntimeProfileTestService


def _mock_http(monkeypatch, handler):
    """Route every AsyncClient the service opens through ``handler``; returns the requests seen."""
    seen = []
    real_client = httpx.AsyncClient

    def _handler(request):
        seen.append(request)
        return handler(request)

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(test_service_module.httpx, "AsyncClient", _factory)
    return seen


def _basic(request):
    header = request.headers.get("Authorization", "")
    assert header.startswith("Basic "), header
    return base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ picking


def test_default_instance_is_the_named_one_else_the_first_usable():
    pick = RuntimeProfileTestService._default_instance
    instances = [
        {"name": "first", "url": "https://first"},
        {"name": "prod", "url": "https://prod"},
    ]
    assert pick({"default_instance": "PROD", "instances": instances})["name"] == "prod"
    assert pick({"instances": instances})["name"] == "first"
    # A disabled default is skipped, like the CLI would; a disabled-only
    # section still yields something to report on.
    assert pick({"default_instance": "prod", "instances": [instances[0], {**instances[1], "enabled": False}]})["name"] == "first"
    assert pick({"instances": [{"name": "off", "url": "https://off", "enabled": False}]})["name"] == "off"
    assert pick({"instances": []}) is None
    assert pick({}) is None


@pytest.mark.parametrize(
    "target,section",
    [("jenkins", "jenkins"), ("nexus", "nexus"), ("splunk", "splunk"), ("pgsql", "pgsql")],
)
def test_run_test_dispatches_each_target_and_requires_the_section_enabled(target, section):
    ok, message = _run(RuntimeProfileTestService().run_test(target, {section: {"enabled": False}}))
    assert ok is False
    assert f"{section}.enabled=true" in message


# ------------------------------------------------------------------ jenkins


def test_jenkins_calls_whoami_with_basic_username_token(monkeypatch):
    def handler(request):
        assert request.url.path == "/whoAmI/api/json"
        assert _basic(request) == "ci-bot:ci-token"
        return httpx.Response(200, json={"name": "ci-bot", "authenticated": True})

    seen = _mock_http(monkeypatch, handler)
    ok, message = _run(
        RuntimeProfileTestService()._test_jenkins(
            {
                "jenkins": {
                    "enabled": True,
                    "default_instance": "release",
                    "instances": [
                        {"name": "ci", "url": "https://ci.example.test", "username": "other", "password": "other-pass"},
                        {"name": "release", "url": "https://release.example.test/", "username": "ci-bot", "password": "ci-pass", "token": "ci-token"},
                    ],
                }
            }
        )
    )
    assert ok is True, message
    assert message == "Jenkins connection OK for release as ci-bot."
    assert len(seen) == 1 and seen[0].url.host == "release.example.test"


def test_jenkins_reports_an_anonymous_answer_rather_than_calling_it_signed_in(monkeypatch):
    _mock_http(monkeypatch, lambda request: httpx.Response(200, json={"name": "anonymous", "authenticated": False}))
    ok, message = _run(
        RuntimeProfileTestService()._test_jenkins(
            {"jenkins": {"enabled": True, "instances": [{"name": "ci", "url": "https://ci.example.test"}]}}
        )
    )
    assert ok is True
    assert "not authenticated" in message


def test_jenkins_rejected_credentials_fail(monkeypatch):
    _mock_http(monkeypatch, lambda request: httpx.Response(401, text="Unauthorized"))
    ok, message = _run(
        RuntimeProfileTestService()._test_jenkins(
            {"jenkins": {"enabled": True, "instances": [{"name": "ci", "url": "https://ci.example.test", "username": "u", "password": "bad"}]}}
        )
    )
    assert ok is False
    assert message.startswith("HTTP 401")
    assert "bad" not in message


def test_jenkins_needs_an_instance_with_a_url():
    ok, message = _run(RuntimeProfileTestService()._test_jenkins({"jenkins": {"enabled": True, "instances": [{"name": "ci"}]}}))
    assert ok is False and "No usable Jenkins instance" in message


# -------------------------------------------------------------------- nexus


def test_nexus_lists_repositories_with_basic_auth(monkeypatch):
    def handler(request):
        assert request.url.path == "/service/rest/v1/repositories"
        assert _basic(request) == "svc-reader:user-token"
        return httpx.Response(200, json=[{"name": "maven-releases"}, {"name": "npm-proxy"}, {"name": "docker-hosted"}])

    _mock_http(monkeypatch, handler)
    ok, message = _run(
        RuntimeProfileTestService()._test_nexus(
            {
                "nexus": {
                    "enabled": True,
                    "instances": [
                        {"name": "main", "url": "https://nexus.example.test/", "username": "svc-reader", "password": "pw", "token": "user-token"}
                    ],
                }
            }
        )
    )
    assert ok is True, message
    assert message == "Nexus connection OK for main (authenticated): 3 repositories visible."


def test_nexus_falls_back_to_anonymous_without_credentials(monkeypatch):
    def handler(request):
        assert "Authorization" not in request.headers
        return httpx.Response(200, json=[])

    _mock_http(monkeypatch, handler)
    ok, message = _run(
        RuntimeProfileTestService()._test_nexus({"nexus": {"enabled": True, "instances": [{"name": "main", "url": "https://nexus.example.test"}]}})
    )
    assert ok is True
    assert "(anonymous): 0 repositories" in message


def test_nexus_rejected_credentials_fail(monkeypatch):
    _mock_http(monkeypatch, lambda request: httpx.Response(401, json={"message": "Unauthorized"}))
    ok, message = _run(
        RuntimeProfileTestService()._test_nexus(
            {"nexus": {"enabled": True, "instances": [{"name": "main", "url": "https://nexus.example.test", "username": "u", "password": "p"}]}}
        )
    )
    assert ok is False and message == "HTTP 401: Unauthorized"


# ------------------------------------------------------------------- splunk


def test_splunk_uses_a_bearer_token_against_the_management_api(monkeypatch):
    def handler(request):
        assert request.url.path == "/services/authentication/current-context"
        assert request.url.params["output_mode"] == "json"
        assert request.url.port == 8089
        assert request.headers["Authorization"] == "Bearer splunk-token"
        return httpx.Response(200, json={"entry": [{"name": "context", "content": {"username": "efp-reader"}}]})

    _mock_http(monkeypatch, handler)
    ok, message = _run(
        RuntimeProfileTestService()._test_splunk(
            {
                "splunk": {
                    "enabled": True,
                    "instances": [{"name": "prod", "url": "https://splunk-api.example.test:8089", "token": "splunk-token"}],
                }
            }
        )
    )
    assert ok is True, message
    assert message == "Splunk connection OK for prod as efp-reader."


def test_splunk_uses_basic_auth_when_only_a_username_and_password_are_set(monkeypatch):
    def handler(request):
        assert _basic(request) == "admin:pw"
        return httpx.Response(200, json={"entry": []})

    _mock_http(monkeypatch, handler)
    ok, message = _run(
        RuntimeProfileTestService()._test_splunk(
            {"splunk": {"enabled": True, "instances": [{"name": "prod", "url": "https://s:8089", "username": "admin", "password": "pw"}]}}
        )
    )
    assert ok is True
    assert message == "Splunk connection OK for prod as admin."


def test_splunk_needs_a_token_or_a_username_and_password():
    ok, message = _run(
        RuntimeProfileTestService()._test_splunk({"splunk": {"enabled": True, "instances": [{"name": "prod", "url": "https://s:8089", "username": "admin"}]}})
    )
    assert ok is False and "authentication token" in message


def test_splunk_transport_errors_are_reported_without_the_token(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("certificate verify failed")

    _mock_http(monkeypatch, handler)
    ok, message = _run(
        RuntimeProfileTestService()._test_splunk(
            {"splunk": {"enabled": True, "instances": [{"name": "prod", "url": "https://s:8089", "token": "splunk-token"}]}}
        )
    )
    assert ok is False
    assert "certificate verify failed" in message
    assert "splunk-token" not in message


# -------------------------------------------------------------------- pgsql


def test_pgsql_checks_tcp_reachability_only_and_points_at_the_runtime_check(monkeypatch):
    seen = {}

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_connect(address, timeout=None):
        seen["address"] = address
        seen["timeout"] = timeout
        return _Conn()

    monkeypatch.setattr(test_service_module.socket, "create_connection", fake_connect)
    ok, message = _run(
        RuntimeProfileTestService()._test_pgsql(
            {
                "pgsql": {
                    "enabled": True,
                    "default_instance": "orders-uat",
                    "instances": [
                        {"name": "other", "host": "other.example.test", "port": 5433, "database": "o", "username": "u"},
                        {"name": "orders-uat", "host": "orders-uat.example.test", "port": 5432, "database": "orders", "username": "ro", "password": "pg-pass"},
                    ],
                }
            }
        )
    )
    assert ok is True, message
    assert seen["address"] == ("orders-uat.example.test", 5432)
    assert seen["timeout"] == 5
    assert message == (
        "PostgreSQL TCP reachability OK for orders-uat: orders-uat.example.test:5432. "
        "Credentials are verified inside the runtime by `pgsql auth test`."
    )
    assert "pg-pass" not in message


def test_pgsql_defaults_the_port_and_reports_a_refused_connection(monkeypatch):
    def fake_connect(address, timeout=None):
        assert address == ("db.example.test", 5432)
        raise OSError("connection refused")

    monkeypatch.setattr(test_service_module.socket, "create_connection", fake_connect)
    ok, message = _run(
        RuntimeProfileTestService()._test_pgsql(
            {"pgsql": {"enabled": True, "instances": [{"name": "db", "host": "db.example.test", "database": "o", "username": "u"}]}}
        )
    )
    assert ok is False
    assert message == "PostgreSQL connection failed for db at db.example.test:5432: connection refused"


def test_pgsql_needs_an_instance_with_a_host():
    ok, message = _run(RuntimeProfileTestService()._test_pgsql({"pgsql": {"enabled": True, "instances": [{"name": "db"}]}}))
    assert ok is False and "No usable PostgreSQL instance" in message


# ------------------------------------------------------------ older helpers


def test_dict_only_helper_still_narrows_to_objects(monkeypatch):
    # jira/confluence/llm read a JSON object; a list answer must not leak
    # through as data to them.
    _mock_http(monkeypatch, lambda request: httpx.Response(200, json=[1, 2, 3]))
    ok, message, data = _run(
        RuntimeProfileTestService()._http_json_request(method="GET", url="https://x", headers={}, payload=None, timeout=5.0)
    )
    assert ok is True and message == "ok" and data is None
