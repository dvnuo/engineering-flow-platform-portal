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


def test_pgsql_probe_does_not_stall_the_event_loop(monkeypatch):
    """The connect runs in a thread: other requests keep being served while an
    unreachable database takes its time to fail."""
    import asyncio
    import time

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def slow_connect(address, timeout=None):
        time.sleep(0.6)
        return _Conn()

    monkeypatch.setattr(test_service_module.socket, "create_connection", slow_connect)

    async def scenario():
        stop = asyncio.Event()

        async def ticker():
            worst, last = 0.0, time.monotonic()
            while not stop.is_set():
                await asyncio.sleep(0.02)
                now = time.monotonic()
                worst, last = max(worst, now - last), now
            return worst

        ticking = asyncio.create_task(ticker())
        await asyncio.sleep(0.05)
        ok, _message = await RuntimeProfileTestService()._test_pgsql(
            {"pgsql": {"enabled": True, "instances": [{"name": "db", "host": "db.example.test"}]}}
        )
        stop.set()
        return ok, await ticking

    ok, worst_stall = asyncio.run(scenario())
    assert ok is True
    assert worst_stall < 0.3, f"the event loop stalled for {worst_stall:.2f}s"


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


# ------------------------------------------------------- pgsql through a proxy

import socket
import threading


class _ConnectProxy:
    """A CONNECT proxy on 127.0.0.1 of the kind the Proxy connector names: it
    records what it was asked for and answers every tunnel with ``status``."""

    def __init__(self, status="200 Connection established"):
        self.status = status
        self.requests = []
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(5)
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def _serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(2)
                data = b""
                try:
                    while b"\r\n\r\n" not in data:
                        chunk = conn.recv(1024)
                        if not chunk:
                            break
                        data += chunk
                    self.requests.append(data.decode("latin-1"))
                    conn.sendall(f"HTTP/1.1 {self.status}\r\nContent-Length: 0\r\n\r\n".encode("ascii"))
                except OSError:
                    pass

    def close(self):
        self.server.close()


@pytest.fixture
def connect_proxy():
    proxy = _ConnectProxy()
    yield proxy
    proxy.close()


@pytest.fixture
def refusing_proxy():
    proxy = _ConnectProxy(status="403 Forbidden")
    yield proxy
    proxy.close()


def _pgsql_config(connector=None, **instance):
    row = {"name": "orders-uat", "host": "orders-uat.internal.test", "database": "orders", "username": "ro", "password": "pg-pass"}
    row.update(instance)
    config = {"pgsql": {"enabled": True, "instances": [row]}}
    if connector is not None:
        config["proxy"] = connector
    return config


def _connector(url, **extra):
    return {"enabled": True, "url": url, "username": "svc", "password": "proxy-pass", **extra}


class _DirectConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _record_direct_connects(monkeypatch):
    seen = []

    def fake_connect(address, timeout=None):
        seen.append(address)
        return _DirectConn()

    monkeypatch.setattr(test_service_module.socket, "create_connection", fake_connect)
    return seen


def test_pgsql_probes_through_the_proxy_connector_as_the_runtime_would(connect_proxy):
    """The runtime hands the Proxy connector to the pgsql CLI as HTTPS_PROXY,
    and the CLI tunnels through it with CONNECT; the probe takes the same
    path, so a host only the proxy can resolve is still checked."""
    ok, message = _run(
        RuntimeProfileTestService()._test_pgsql(
            _pgsql_config(connector=_connector(connect_proxy.url, no_proxy=".svc.cluster.local"))
        )
    )
    assert ok is True, message
    assert message == (
        "PostgreSQL TCP reachability OK for orders-uat: orders-uat.internal.test:5432 "
        f"through the proxy at 127.0.0.1:{connect_proxy.port} (the Proxy connector). "
        "Credentials are verified inside the runtime by `pgsql auth test`."
    )
    request = connect_proxy.requests[0]
    assert request.startswith("CONNECT orders-uat.internal.test:5432 HTTP/1.1\r\n"), "the host name goes to the proxy unresolved"
    assert "Proxy-Authorization: Basic " + base64.b64encode(b"svc:proxy-pass").decode("ascii") + "\r\n" in request
    assert "proxy-pass" not in message and "pg-pass" not in message


def test_pgsql_takes_credentials_already_in_the_connector_url(connect_proxy):
    config = _pgsql_config(connector={"enabled": True, "url": f"http://u%40corp:p%3Aw@127.0.0.1:{connect_proxy.port}"})
    ok, message = _run(RuntimeProfileTestService()._test_pgsql(config))
    assert ok is True, message
    assert "Proxy-Authorization: Basic " + base64.b64encode(b"u@corp:p:w").decode("ascii") in connect_proxy.requests[0]
    assert "p%3Aw" not in message and "p:w" not in message and "u@corp" not in message


def test_pgsql_no_proxy_of_the_connector_keeps_the_probe_direct(monkeypatch):
    seen = _record_direct_connects(monkeypatch)
    service = RuntimeProfileTestService()
    unreachable = _connector("http://proxy.unreachable.test:3128", no_proxy="localhost,.internal.test")

    ok, message = _run(service._test_pgsql(_pgsql_config(connector=unreachable)))
    assert ok is True, message
    assert seen == [("orders-uat.internal.test", 5432)]
    assert "(direct: NO_PROXY of the Proxy connector exempts this host)" in message

    # Without a NO_PROXY of its own the connector gets the runtime's default
    # list, which keeps in-cluster names off the proxy.
    ok, message = _run(
        service._test_pgsql(_pgsql_config(connector=_connector("http://proxy.unreachable.test:3128"), host="pg.efp.svc.cluster.local"))
    )
    assert ok is True and seen[-1] == ("pg.efp.svc.cluster.local", 5432)
    assert "exempts this host" in message

    # A connector that is off, or names no URL, is a direct connection, worded
    # as before.
    for connector in ({"enabled": False, "url": "http://proxy.unreachable.test:3128"}, {"enabled": True, "url": ""}):
        ok, message = _run(service._test_pgsql(_pgsql_config(connector=connector)))
        assert ok is True
        assert message == (
            "PostgreSQL TCP reachability OK for orders-uat: orders-uat.internal.test:5432. "
            "Credentials are verified inside the runtime by `pgsql auth test`."
        )


def test_pgsql_instance_proxy_none_forces_a_direct_probe(monkeypatch):
    seen = _record_direct_connects(monkeypatch)
    ok, message = _run(
        RuntimeProfileTestService()._test_pgsql(
            _pgsql_config(connector=_connector("http://proxy.unreachable.test:3128"), proxy="none")
        )
    )
    assert ok is True, message
    assert seen == [("orders-uat.internal.test", 5432)]
    assert "(direct: the instance proxy is none)" in message


def test_pgsql_instance_proxy_url_is_used_instead_of_the_connector(connect_proxy):
    config = _pgsql_config(connector=_connector("http://proxy.unreachable.test:3128"), proxy=connect_proxy.url)
    ok, message = _run(RuntimeProfileTestService()._test_pgsql(config))
    assert ok is True, message
    assert f"through the proxy at 127.0.0.1:{connect_proxy.port} (the instance proxy)" in message
    # The connector's credentials are for the connector's proxy, not this one.
    assert "Proxy-Authorization" not in connect_proxy.requests[0]

    # A bare host:port is an http proxy, as the CLI reads it.
    ok, message = _run(RuntimeProfileTestService()._test_pgsql(_pgsql_config(proxy=f"127.0.0.1:{connect_proxy.port}")))
    assert ok is True, message
    assert len(connect_proxy.requests) == 2


def test_pgsql_reports_a_proxy_that_refuses_the_tunnel(refusing_proxy):
    ok, message = _run(RuntimeProfileTestService()._test_pgsql(_pgsql_config(connector=_connector(refusing_proxy.url))))
    assert ok is False
    assert message == (
        "PostgreSQL connection failed for orders-uat at orders-uat.internal.test:5432 "
        f"through the proxy at 127.0.0.1:{refusing_proxy.port} (the Proxy connector): "
        f"proxy 127.0.0.1:{refusing_proxy.port} answered CONNECT orders-uat.internal.test:5432 with 403 Forbidden"
    )
    assert "proxy-pass" not in message


def test_pgsql_reports_a_proxy_that_is_not_there():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    ok, message = _run(RuntimeProfileTestService()._test_pgsql(_pgsql_config(connector=_connector(f"http://127.0.0.1:{port}"))))
    assert ok is False
    assert f"through the proxy at 127.0.0.1:{port} (the Proxy connector): " in message
    assert "proxy-pass" not in message


def test_pgsql_refuses_a_proxy_it_cannot_tunnel_through():
    ok, message = _run(RuntimeProfileTestService()._test_pgsql(_pgsql_config(proxy="socks5://proxy.example.test:1080")))
    assert ok is False
    assert "http:// or https://" in message


@pytest.mark.parametrize(
    "host,no_proxy,exempt",
    [
        # NO_PROXY the way Go's ProxyFromEnvironment reads it, which the
        # tools follow on the environment path: a bare domain covers its
        # subdomains, a leading dot (or *.) the subdomains only.
        ("db.internal.test", "localhost,.internal.test", True),
        ("internal.test", ".internal.test", False),
        ("db.internal.test", "internal.test", True),
        ("db.internal.test", "*.internal.test", True),
        ("internal.test", "*.internal.test", False),
        ("db.internal.test", "db.internal.test:5432", True),
        ("db.internal.test", "https://db.internal.test", True),
        ("db.internal.test", "*", True),
        ("db.internal.test", "other.test, .corp", False),
        ("db.internal.test", "", False),
        ("10.1.2.3", "10.0.0.0/8", True),
        ("11.1.2.3", "10.0.0.0/8", False),
        ("pg.efp.svc.cluster.local", "", True),
        ("169.254.169.254", None, True),
        ("localhost", "nothing", True),
    ],
)
def test_no_proxy_patterns_follow_the_tools_http_clients(host, no_proxy, exempt):
    assert RuntimeProfileTestService._no_proxy_exempts(host, no_proxy) is exempt
