from pathlib import Path


def _proxy_source() -> str:
    return Path("app/api/proxy.py").read_text(encoding="utf-8")


def _extract_function(source: str, signature: str, next_marker: str) -> str:
    start = source.index(signature)
    end = source.index(next_marker, start)
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


def test_proxy_websocket_uses_runtime_trace_headers_for_connect():
    source = _proxy_source()

    assert "websockets.connect(" in source
    assert "additional_headers=build_runtime_trace_headers(get_log_context())" in source


def test_proxy_websocket_runtime_url_failure_closes_1011():
    source = _proxy_source()

    assert "base = proxy_service.build_agent_base_url(agent).rstrip(\"/\")" in source
    assert 'await websocket.close(code=1011, reason="Runtime URL unavailable")' in source


def test_proxy_websocket_error_fallback_closes_1011():
    source = _proxy_source()

    assert "except Exception:" in source
    assert "await websocket.close(code=1011)" in source
