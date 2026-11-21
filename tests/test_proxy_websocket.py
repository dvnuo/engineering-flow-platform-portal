from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def _proxy_source() -> str:
    return Path("app/api/proxy.py").read_text(encoding="utf-8")


def _extract_function(source: str, signature: str, next_marker: str) -> str:
    start = source.index(signature)
    end = source.find(next_marker, start)
    if end == -1:
        end = len(source)
    return source[start:end]


def test_proxy_websocket_route_contract_exists():
    source = _proxy_source()

    assert '@router.websocket("/a/{agent_id}/api/events")' in source
    assert "async def proxy_agent_events(agent_id: str, websocket: WebSocket):" in source


def test_proxy_websocket_auth_contract_cookie_or_query_token():
    source = _proxy_source()

    # The cookie's name depends on the deployment (__Host- prefixed over https),
    # so it is read through the one helper that knows it.
    assert "token = read_session_token(websocket.cookies)" in source
    assert "settings.session_cookie_name" not in source
    assert 'token = websocket.query_params.get("token")' in source
    assert 'await websocket.close(code=4401, reason="Not authenticated")' in source
    assert 'await websocket.close(code=4401, reason="User is not allowlisted")' in source


def test_filter_proxy_query_items_strips_token_but_keeps_session_id():
    source = _proxy_source()
    fn_source = _extract_function(source, "def _filter_proxy_query_items(query_items):", "\n\ndef _safe_download_filename")
    namespace: dict = {}
    exec(fn_source, namespace)
    filter_query_items = namespace["_filter_proxy_query_items"]

    filtered = filter_query_items([
        ("token", "drop"),
        ("Token", "drop-too"),
        ("session_id", "sess-42"),
        ("stream", "runtime"),
    ])
    assert filtered == [("session_id", "sess-42"), ("stream", "runtime")]


def test_proxy_websocket_upstream_url_contract_uses_api_events_and_filtered_query():
    source = _proxy_source()

    assert 'upstream_url = f"{ws_base}/api/events"' in source
    assert "query_items = _filter_proxy_query_items(websocket.query_params.multi_items())" in source
    assert 'upstream_url = f"{upstream_url}?{urlencode(query_items)}"' in source


def test_proxy_websocket_uses_runtime_trace_and_identity_headers_for_connect():
    source = _proxy_source()

    assert "websockets.connect(" in source
    assert "**build_runtime_trace_headers(get_log_context())" in source
    assert "**build_portal_agent_headers(user, agent)" in source


def test_proxy_websocket_connect_uses_compat_header_kwargs_helper():
    source = _proxy_source()

    assert "def _websocket_connect_header_kwargs" in source
    assert "**_websocket_connect_header_kwargs(upstream_headers)" in source
    assert "upstream_headers = {" in source
    assert "**build_runtime_trace_headers(get_log_context())" in source
    assert "**build_portal_agent_headers(user, agent)" in source


def test_proxy_websocket_runtime_url_failure_closes_1011():
    source = _proxy_source()

    assert "base = proxy_service.build_agent_base_url(agent).rstrip(\"/\")" in source
    assert 'await websocket.close(code=1011, reason="Runtime URL unavailable")' in source


def test_proxy_websocket_rejects_revoked_allowlist_access(monkeypatch):
    from app.main import app
    import app.api.proxy as proxy_module

    fake_user = SimpleNamespace(id=77, username="revoked-user", role="user", is_active=True)
    monkeypatch.setattr(proxy_module, "parse_session_token", lambda _token: 77)
    monkeypatch.setattr(
        proxy_module,
        "UserRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _user_id: fake_user),
    )
    monkeypatch.setattr(
        proxy_module,
        "UserAllowlistRepository",
        lambda _db: SimpleNamespace(get_active_by_username=lambda _username: None),
    )
    monkeypatch.setattr(proxy_module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/a/agent-1/api/events?token=secret"):
            pass
    assert exc_info.value.code == 4401


def test_proxy_websocket_error_fallback_closes_1011():
    source = _proxy_source()

    assert "except Exception:" in source
    assert "await websocket.close(code=1011)" in source


def test_proxy_websocket_generates_portal_trace_id_instead_of_trusting_browser_header():
    source = _proxy_source()
    fn_source = _extract_function(
        source,
        "async def proxy_agent_events(agent_id: str, websocket: WebSocket):",
        "\n\n    finally:\n        reset_log_context(context_token)",
    )
    assert "trace_id = generate_trace_id()" in fn_source
    assert "websocket.headers.get(\"X-Trace-Id\")" not in fn_source
    assert "websocket.headers.get(\"X-Request-Id\")" not in fn_source


def test_proxy_websocket_forwards_filtered_query_params_to_runtime(monkeypatch):
    from app.main import app
    import app.api.proxy as proxy_module

    fake_user = SimpleNamespace(id=77, username="runtime-user", nickname="Runtime User", role="user", is_active=True)
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=77, visibility="private", status="running", name="Agent One")
    captured = {}

    class _FakeUpstream:
        def __aiter__(self):
            self._sent = False
            return self

        async def __anext__(self):
            if self._sent:
                raise StopAsyncIteration
            self._sent = True
            return '{"event_type":"heartbeat"}'

        async def send(self, message):
            captured.setdefault("sent", []).append(message)

    class _FakeConnect:
        async def __aenter__(self):
            return _FakeUpstream()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def _fake_connect(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _FakeConnect()

    monkeypatch.setattr(proxy_module, "parse_session_token", lambda token: "user-77")
    monkeypatch.setattr(proxy_module, "UserRepository", lambda _db: SimpleNamespace(get_by_id=lambda _user_id: fake_user))
    monkeypatch.setattr(proxy_module, "UserAllowlistRepository", lambda _db: SimpleNamespace(get_active_by_username=lambda _username: object()))
    monkeypatch.setattr(proxy_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent))
    monkeypatch.setattr(proxy_module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(proxy_module.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime.local:8000")
    monkeypatch.setattr(proxy_module.websockets, "connect", _fake_connect)

    client = TestClient(app)
    with client.websocket_connect(
        "/a/agent-1/api/events?token=secret&session_id=s1&request_id=r1&replay=1&types=llm_thinking%2Ctool.started"
    ) as websocket:
        assert websocket.receive_text() == '{"event_type":"heartbeat"}'

    assert captured["url"] == (
        "ws://runtime.local:8000/api/events?"
        "session_id=s1&request_id=r1&replay=1&types=llm_thinking%2Ctool.started"
    )
    assert "token=secret" not in captured["url"]
    assert captured["kwargs"]


def test_events_ws_proxy_forwards_query_string_including_last_event_at(monkeypatch):
    from app.main import app
    import app.api.proxy as proxy_module

    fake_user = SimpleNamespace(id=77, username="runtime-user", nickname="Runtime User", role="user", is_active=True)
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=77, visibility="private", status="running", name="Agent One")
    captured = {}

    class _FakeUpstream:
        def __aiter__(self):
            self._sent = False
            return self

        async def __anext__(self):
            if self._sent:
                raise StopAsyncIteration
            self._sent = True
            return '{"event_type":"heartbeat"}'

        async def send(self, message):
            captured.setdefault("sent", []).append(message)

    class _FakeConnect:
        async def __aenter__(self):
            return _FakeUpstream()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def _fake_connect(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _FakeConnect()

    monkeypatch.setattr(proxy_module, "parse_session_token", lambda token: "user-77")
    monkeypatch.setattr(proxy_module, "UserRepository", lambda _db: SimpleNamespace(get_by_id=lambda _user_id: fake_user))
    monkeypatch.setattr(proxy_module, "UserAllowlistRepository", lambda _db: SimpleNamespace(get_active_by_username=lambda _username: object()))
    monkeypatch.setattr(proxy_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent))
    monkeypatch.setattr(proxy_module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(proxy_module.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime.local:8000")
    monkeypatch.setattr(proxy_module.websockets, "connect", _fake_connect)

    client = TestClient(app)
    with client.websocket_connect(
        "/a/agent-1/api/events?"
        "token=secret&session_id=s1&request_id=r1&replay=1&replay_limit=50&"
        "types=llm_thinking%2Ctool.started&last_event_at=2026-05-18T12%3A00%3A00Z"
    ) as websocket:
        assert websocket.receive_text() == '{"event_type":"heartbeat"}'

    parsed = urlsplit(captured["url"])
    assert parsed.scheme == "ws"
    assert parsed.netloc == "runtime.local:8000"
    assert parsed.path == "/api/events"
    query = dict(parse_qsl(parsed.query))
    assert query == {
        "session_id": "s1",
        "request_id": "r1",
        "replay": "1",
        "replay_limit": "50",
        "types": "llm_thinking,tool.started",
        "last_event_at": "2026-05-18T12:00:00Z",
    }
    assert "token" not in query
    assert captured["kwargs"]


# --- the handshake's Origin ---------------------------------------------------
#
# A browser sends the session cookie on a WebSocket handshake to the Portal
# from any page of the same site, a sibling subdomain's included, and states
# that page's origin. Only the Portal's own pages may open the events stream.
# The rule is the HTTP guard's: Sec-Fetch-Site decides when the handshake
# carries it, the Origin (against Host and BASE_URI) when it does not.


def _ws_harness(monkeypatch, captured):
    import app.api.proxy as proxy_module

    fake_user = SimpleNamespace(id=77, username="runtime-user", nickname="Runtime User", role="user", is_active=True)
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=77, visibility="private", status="running", name="Agent One")

    class _FakeUpstream:
        def __aiter__(self):
            self._sent = False
            return self

        async def __anext__(self):
            if self._sent:
                raise StopAsyncIteration
            self._sent = True
            return '{"event_type":"heartbeat"}'

        async def send(self, message):
            captured.setdefault("sent", []).append(message)

    class _FakeConnect:
        async def __aenter__(self):
            return _FakeUpstream()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def _fake_connect(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _FakeConnect()

    def _session_factory():
        captured["db_opened"] = captured.get("db_opened", 0) + 1
        return SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(proxy_module, "parse_session_token", lambda token: "user-77")
    monkeypatch.setattr(proxy_module, "UserRepository", lambda _db: SimpleNamespace(get_by_id=lambda _user_id: fake_user))
    monkeypatch.setattr(proxy_module, "UserAllowlistRepository", lambda _db: SimpleNamespace(get_active_by_username=lambda _username: object()))
    monkeypatch.setattr(proxy_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent))
    monkeypatch.setattr(proxy_module, "SessionLocal", _session_factory)
    monkeypatch.setattr(proxy_module.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime.local:8000")
    monkeypatch.setattr(proxy_module.websockets, "connect", _fake_connect)
    return proxy_module


@pytest.mark.parametrize(
    "origin",
    ["http://app.testserver", "https://evil.example.test", "http://testserver:8443", "null"],
)
def test_proxy_websocket_refuses_a_handshake_from_another_origin(monkeypatch, origin):
    from app.main import app

    captured = {}
    _ws_harness(monkeypatch, captured)
    client = TestClient(app)
    client.cookies.set("portal_session", "valid-session")
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/a/agent-1/api/events", headers={"Origin": origin}):
            pass
    assert exc_info.value.code == 4403
    # Refused before anything was looked up or the runtime was dialled.
    assert "db_opened" not in captured
    assert "url" not in captured


@pytest.mark.parametrize("headers", [{"Origin": "http://testserver"}, {}])
def test_proxy_websocket_accepts_the_portals_own_origin_and_clients_without_one(monkeypatch, headers):
    from app.main import app

    captured = {}
    _ws_harness(monkeypatch, captured)
    client = TestClient(app)
    client.cookies.set("portal_session", "valid-session")
    with client.websocket_connect("/a/agent-1/api/events?session_id=s1", headers=headers) as websocket:
        assert websocket.receive_text() == '{"event_type":"heartbeat"}'
    assert captured["url"] == "ws://runtime.local:8000/api/events?session_id=s1"


def test_proxy_websocket_accepts_the_origin_named_by_base_uri(monkeypatch):
    from app.main import app

    captured = {}
    proxy_module = _ws_harness(monkeypatch, captured)
    # Behind an ingress the Host header may be the service's, while the page's
    # origin is the public one.
    monkeypatch.setattr(proxy_module.settings, "base_uri", "http://portal.example.test")
    client = TestClient(app)
    client.cookies.set("portal_session", "valid-session")
    with client.websocket_connect("/a/agent-1/api/events", headers={"Origin": "http://portal.example.test"}) as websocket:
        assert websocket.receive_text() == '{"event_type":"heartbeat"}'


def test_proxy_websocket_accepts_a_same_origin_page_behind_a_proxy_that_replaces_host(monkeypatch):
    from app.main import app

    captured = {}
    proxy_module = _ws_harness(monkeypatch, captured)
    # A reverse proxy handed the handshake on under its own Host, and BASE_URI
    # does not name the address members type: Origin matches nothing the
    # Portal knows. What the browser says about the page still decides.
    monkeypatch.setattr(proxy_module.settings, "base_uri", "")
    client = TestClient(app)
    client.cookies.set("portal_session", "valid-session")
    headers = {
        "Host": "efp-portal-service.default.svc.cluster.local",
        "Origin": "https://portal.example.test",
        "Sec-Fetch-Site": "same-origin",
    }
    with client.websocket_connect("/a/agent-1/api/events?session_id=s1", headers=headers) as websocket:
        assert websocket.receive_text() == '{"event_type":"heartbeat"}'
    assert captured["url"] == "ws://runtime.local:8000/api/events?session_id=s1"


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "same-site", "Origin": "http://app.testserver"},
        {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example.test"},
        {"Sec-Fetch-Site": "cross-site"},
        # Sec-Fetch-Site decides when the handshake carries it, whatever Origin says.
        {"Sec-Fetch-Site": "same-site", "Origin": "http://testserver"},
    ],
)
def test_proxy_websocket_refuses_a_handshake_the_browser_calls_same_site_or_cross_site(monkeypatch, headers):
    from app.main import app

    captured = {}
    _ws_harness(monkeypatch, captured)
    client = TestClient(app)
    client.cookies.set("portal_session", "valid-session")
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/a/agent-1/api/events", headers=headers):
            pass
    assert exc_info.value.code == 4403
    assert "db_opened" not in captured
    assert "url" not in captured


def test_proxy_websocket_falls_back_to_the_origin_when_the_handshake_has_no_sec_fetch_site(monkeypatch):
    from app.main import app

    captured = {}
    proxy_module = _ws_harness(monkeypatch, captured)
    monkeypatch.setattr(proxy_module.settings, "base_uri", "")
    client = TestClient(app)
    client.cookies.set("portal_session", "valid-session")
    # Without the header the proxy's Host is all there is to compare with:
    # this is the case BASE_URI exists for (k8s/README.md).
    headers = {"Host": "efp-portal-service.default.svc.cluster.local", "Origin": "http://portal.example.test"}
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/a/agent-1/api/events", headers=headers):
            pass
    assert exc_info.value.code == 4403

    # (An http address: with an https one the session cookie would be the
    # host-bound one, which this client does not hold.)
    monkeypatch.setattr(proxy_module.settings, "base_uri", "http://portal.example.test")
    with client.websocket_connect("/a/agent-1/api/events", headers=headers) as websocket:
        assert websocket.receive_text() == '{"event_type":"heartbeat"}'


def test_proxy_websocket_refusal_logs_what_the_browser_and_the_proxy_said(monkeypatch, caplog):
    import logging

    from app.main import app

    captured = {}
    _ws_harness(monkeypatch, captured)
    client = TestClient(app)
    client.cookies.set("portal_session", "valid-session")
    headers = {"Host": "internal.name:8000", "Origin": "https://portal.example.test forged=1", "Sec-Fetch-Site": "same-site"}
    with caplog.at_level(logging.WARNING, logger="app.api.proxy"):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/a/agent-1/api/events", headers=headers):
                pass
    (message,) = [record.getMessage() for record in caplog.records if "WebSocket handshake refused" in record.getMessage()]
    assert "agent_id=agent-1" in message
    assert "sec_fetch_site=same-site" in message
    assert "host=internal.name:8000" in message
    # A space in a header cannot start a field of the caller's own.
    assert "origin=https://portal.example.test_forged=1" in message


def test_proxy_websocket_origin_check_follows_the_guard_setting(monkeypatch):
    from app.main import app

    captured = {}
    proxy_module = _ws_harness(monkeypatch, captured)
    monkeypatch.setattr(proxy_module.settings, "origin_guard_enabled", False)
    client = TestClient(app)
    client.cookies.set("portal_session", "valid-session")
    with client.websocket_connect("/a/agent-1/api/events", headers={"Origin": "http://app.testserver"}) as websocket:
        assert websocket.receive_text() == '{"event_type":"heartbeat"}'


def test_proxy_websocket_sends_the_assistants_trust_token_upstream(monkeypatch):
    from app.main import app
    from app.services.agent_trust import agent_trust_token

    captured = {}
    _ws_harness(monkeypatch, captured)
    client = TestClient(app)
    client.cookies.set("portal_session", "valid-session")
    with client.websocket_connect("/a/agent-1/api/events") as websocket:
        assert websocket.receive_text() == '{"event_type":"heartbeat"}'
    (upstream_headers,) = captured["kwargs"].values()
    assert upstream_headers["X-Portal-Trust-Token"] == agent_trust_token("agent-1")
    assert upstream_headers["X-Portal-Author-Source"] == "portal"


def test_proxy_websocket_reads_the_hardened_cookie_over_https(monkeypatch):
    from app.main import app

    captured = {}
    proxy_module = _ws_harness(monkeypatch, captured)
    from app.security import session_cookie

    monkeypatch.setattr(proxy_module.settings, "base_uri", "https://testserver")
    monkeypatch.setattr(session_cookie.settings, "base_uri", "https://testserver")
    seen = []
    monkeypatch.setattr(proxy_module, "parse_session_token", lambda token: seen.append(token) or "user-77")

    client = TestClient(app)
    # The cookie from before the upgrade is not a session any more.
    client.cookies.set("portal_session", "old-name")
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/a/agent-1/api/events"):
            pass
    assert exc_info.value.code == 4401
    assert seen == []

    client.cookies.set("__Host-portal_session", "new-name")
    with client.websocket_connect("/a/agent-1/api/events") as websocket:
        assert websocket.receive_text() == '{"event_type":"heartbeat"}'
    assert seen == ["new-name"]
