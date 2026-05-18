from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


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

    assert "token = websocket.cookies.get(settings.session_cookie_name)" in source
    assert 'token = websocket.query_params.get("token")' in source
    assert 'await websocket.close(code=4401, reason="Not authenticated")' in source


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
    assert "**build_portal_agent_identity_headers(user, agent)" in source


def test_proxy_websocket_connect_uses_compat_header_kwargs_helper():
    source = _proxy_source()

    assert "def _websocket_connect_header_kwargs" in source
    assert "**_websocket_connect_header_kwargs(upstream_headers)" in source
    assert "upstream_headers = {" in source
    assert "**build_runtime_trace_headers(get_log_context())" in source
    assert "**build_portal_agent_identity_headers(user, agent)" in source


def test_proxy_websocket_runtime_url_failure_closes_1011():
    source = _proxy_source()

    assert "base = proxy_service.build_agent_base_url(agent).rstrip(\"/\")" in source
    assert 'await websocket.close(code=1011, reason="Runtime URL unavailable")' in source


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
