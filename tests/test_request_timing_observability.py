"""Per-request timing + upstream TTFB observability for the portal.

Portal is not the multi-minute latency users see, but until now it logged
nothing per request and never timed its own upstream agent call, so it hid it.
These tests pin the two lines an operator greps in the pod logs:

    HTTP request end method=... path=... status=... duration_ms=... trace_id=...
    Runtime stream end agent_id=... ttfb_ms=... total_ms=... trace_id=...
"""

import asyncio
import json
import logging
import re
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


def _matching_records(caplog, needle: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if needle in record.getMessage()]


def _records(caplog, needle: str) -> list[str]:
    return [record.getMessage() for record in _matching_records(caplog, needle)]


def _field(message: str, key: str) -> str:
    match = re.search(rf"\b{key}=(\S+)", message)
    assert match, f"missing {key}= in log line: {message}"
    return match.group(1)


def _fields(message: str, key: str) -> list[str]:
    return re.findall(rf"\b{key}=(\S+)", message)


def _install_streaming_agent(
    monkeypatch,
    proxy_module,
    *,
    chunks,
    first_chunk_delay=0.0,
    open_error=None,
    before_first_chunk=None,
):
    fake_user = SimpleNamespace(id=77, username="runtime-user", nickname="Runtime User", role="user")
    fake_agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=77,
        visibility="private",
        status="running",
        name="Agent One",
    )

    monkeypatch.setattr(
        proxy_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )
    monkeypatch.setattr(
        proxy_module.runtime_execution_context_service,
        "build_runtime_metadata",
        lambda _db, _agent: {"runtime_profile_id": "server-runtime"},
    )
    monkeypatch.setattr(
        proxy_module.proxy_service,
        "build_agent_base_url",
        lambda _agent: "http://runtime.local:8000",
    )

    class _FakeUpstreamResponse:
        status_code = 200
        headers = {"content-type": "text/event-stream", "cache-control": "no-cache"}

        async def aiter_raw(self):
            if first_chunk_delay:
                await asyncio.sleep(first_chunk_delay)
            if before_first_chunk is not None:
                before_first_chunk()
            for chunk in chunks:
                yield chunk

    class _FakeStreamContext:
        async def __aenter__(self):
            if open_error is not None:
                raise open_error
            return _FakeUpstreamResponse()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        def stream(self, **_kwargs):
            return _FakeStreamContext()

        async def aclose(self):
            return None

    monkeypatch.setattr(proxy_module.httpx, "AsyncClient", _FakeAsyncClient)

    return fake_user


@pytest.fixture()
def streaming_client(monkeypatch):
    from app.main import app
    import app.api.proxy as proxy_module

    def _make(**kwargs):
        fake_user = _install_streaming_agent(monkeypatch, proxy_module, **kwargs)
        app.dependency_overrides[proxy_module.get_current_user] = lambda: fake_user

        def _override_db():
            yield object()

        app.dependency_overrides[proxy_module.get_db] = _override_db
        return TestClient(app)

    try:
        yield _make
    finally:
        app.dependency_overrides.clear()


def test_http_middleware_logs_one_request_timing_line(caplog):
    from app.main import app

    caplog.set_level(logging.INFO, logger="app.main")
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    lines = _records(caplog, "HTTP request end")
    assert len(lines) == 1
    line = lines[0]
    assert "method=GET" in line
    assert "path=/health" in line
    assert "status=200" in line
    assert float(_field(line, "duration_ms")) >= 0
    assert _field(line, "trace_id") == response.headers["X-Trace-Id"]


FOREIGN_TRACE_ID = "ffffffffffffffffffffffffffffffff"


def test_http_middleware_request_line_uses_local_trace_id_not_the_live_contextvar(caplog):
    """Regression: the log contextvar no longer carries this request's trace id
    by the time the request line is rendered (it is unbound while a
    StreamingResponse body is still draining), so the middleware must log the
    trace id it generated, not whatever the contextvar currently holds."""
    from app.log_context import bind_log_context
    from app.main import bind_request_log_context
    from starlette.requests import Request
    from starlette.responses import Response

    caplog.set_level(logging.INFO, logger="app.main")

    async def _call_next(_request):
        bind_log_context(trace_id=FOREIGN_TRACE_ID)
        return Response(status_code=204)

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/probe/request-timing",
            "raw_path": b"/probe/request-timing",
            "root_path": "",
            "query_string": b"",
            "headers": [],
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
        }
    )
    response = asyncio.run(bind_request_log_context(request, _call_next))

    lines = _records(caplog, "HTTP request end")
    assert len(lines) == 1
    assert "path=/probe/request-timing" in lines[0]
    assert _field(lines[0], "status") == "204"
    trace_id = _field(lines[0], "trace_id")
    assert trace_id == response.headers["X-Trace-Id"]
    assert trace_id != FOREIGN_TRACE_ID


def test_stream_summary_trace_id_comes_from_a_local_not_the_live_contextvar(streaming_client, caplog):
    """The summary line is emitted from the StreamingResponse drain callbacks,
    long after the request's log context stopped being the bound one, so the
    trace id must be captured up-front rather than re-read at emit time."""
    from app.log_context import bind_log_context

    caplog.set_level(logging.INFO)
    client = streaming_client(
        chunks=(b"chunk-1\n", b"chunk-2\n"),
        before_first_chunk=lambda: bind_log_context(trace_id=FOREIGN_TRACE_ID),
    )

    with client.stream(
        "POST",
        "/a/agent-1/api/chat/stream",
        content=b'{"message":"hello","session_id":"sess-9","request_id":"req-9"}',
        headers={"content-type": "application/json"},
    ) as response:
        body = b"".join(response.iter_bytes())

    assert body == b"chunk-1\nchunk-2\n"
    real_trace_id = response.headers["X-Trace-Id"]

    request_lines = _records(caplog, "HTTP request end")
    assert len(request_lines) == 1
    assert "path=/a/agent-1/api/chat/stream" in request_lines[0]
    assert _field(request_lines[0], "trace_id") == real_trace_id

    summary_lines = _records(caplog, "Runtime stream end")
    assert len(summary_lines) == 1
    assert _field(summary_lines[0], "trace_id") == real_trace_id
    assert FOREIGN_TRACE_ID not in summary_lines[0]


def test_chat_stream_logs_ttfb_and_upstream_timings(streaming_client, caplog):
    caplog.set_level(logging.INFO)
    client = streaming_client(chunks=(b"chunk-1\n", b"chunk-2\n"))

    with client.stream(
        "POST",
        "/a/agent-1/api/chat/stream?token=secret",
        content=b'{"message":"hello","session_id":"sess-9","request_id":"req-9"}',
        headers={"content-type": "application/json", "authorization": "Bearer browser-token"},
    ) as response:
        b"".join(response.iter_bytes())

    lines = _records(caplog, "Runtime stream end")
    assert len(lines) == 1
    line = lines[0]
    assert "agent_id=agent-1" in line
    assert "session_id=sess-9" in line
    assert "request_id=req-9" in line
    assert "upstream_url=http://runtime.local:8000/api/chat/stream" in line
    assert "status=200" in line
    assert float(_field(line, "resolve_ms")) >= 0
    assert float(_field(line, "open_ms")) >= 0
    assert float(_field(line, "ttfb_ms")) >= 0
    assert float(_field(line, "total_ms")) >= float(_field(line, "ttfb_ms"))
    assert _field(line, "trace_id") == response.headers["X-Trace-Id"]


def test_chat_stream_ttfb_measures_time_to_first_chunk_not_stream_open(streaming_client, caplog):
    caplog.set_level(logging.INFO)
    client = streaming_client(chunks=(b"chunk-1\n",), first_chunk_delay=0.15)

    with client.stream(
        "POST",
        "/a/agent-1/api/chat/stream",
        content=b'{"message":"hello"}',
        headers={"content-type": "application/json"},
    ) as response:
        b"".join(response.iter_bytes())

    line = _records(caplog, "Runtime stream end")[0]
    # The upstream context manager opens instantly; only the first chunk is slow,
    # which is exactly the native runtime's pre-LLM work we want to attribute.
    assert float(_field(line, "ttfb_ms")) >= 100
    assert float(_field(line, "open_ms")) < 100


def test_chat_stream_summary_line_is_single_line_and_leaks_no_content(streaming_client, caplog):
    caplog.set_level(logging.INFO)
    client = streaming_client(chunks=(b"secret-assistant-reply\n",))

    with client.stream(
        "POST",
        "/a/agent-1/api/chat/stream?token=secret-query-token",
        content=b'{"message":"my private prompt","session_id":"sess-9"}',
        headers={"content-type": "application/json", "authorization": "Bearer browser-token"},
    ) as response:
        b"".join(response.iter_bytes())

    line = _records(caplog, "Runtime stream end")[0]
    assert "\n" not in line
    assert "request_body_bytes=" in line
    for leak in ("my private prompt", "secret-assistant-reply", "browser-token", "secret-query-token"):
        assert leak not in line
    # First-party records only; the httpx client logger used by TestClient
    # itself echoes the request URL and is not portal code.
    joined = "\n".join(
        record.getMessage() for record in caplog.records if record.name.startswith("app.")
    )
    for leak in ("my private prompt", "browser-token", "secret-query-token"):
        assert leak not in joined


def test_client_supplied_session_id_cannot_forge_extra_log_fields(streaming_client, caplog):
    """A client-controlled session_id must not be able to close the field it is
    logged in and open its own -- otherwise it forges status=/total_ms= and, far
    worse, a trace_id that poisons correlation for every other line."""
    caplog.set_level(logging.INFO)
    client = streaming_client(chunks=(b"chunk-1\n",))

    with client.stream(
        "POST",
        "/a/agent-1/api/chat/stream",
        content=(
            b'{"message":"hi","session_id":"sess-1 status=999 total_ms=0 trace_id=forged",'
            b'"request_id":"req-1 agent_id=evil"}'
        ),
        headers={"content-type": "application/json"},
    ) as response:
        b"".join(response.iter_bytes())

    line = _records(caplog, "Runtime stream end")[0]
    assert _fields(line, "trace_id") == [response.headers["X-Trace-Id"]]
    assert _fields(line, "status") == ["200"]
    assert _fields(line, "agent_id") == ["agent-1"]
    assert len(_fields(line, "total_ms")) == 1
    assert "trace_id=forged" not in line
    assert "agent_id=evil" not in line
    # The ids are still logged, just flattened into a single opaque token.
    session_id = _field(line, "session_id")
    assert session_id.startswith("sess-1")
    assert " " not in session_id and "=" not in session_id


def test_loggable_id_neutralises_separators_and_non_scalar_values():
    from app.api.proxy import _loggable_id

    assert _loggable_id("sess-1 status=999") == "sess-1_status_999"
    assert _loggable_id("sess\r\n2026-07-22 ERROR fake log line") == "sess2026-07-22_ERROR_fake_log_line"
    assert _loggable_id({"a": 1}) == "-"
    assert _loggable_id(["a"], fallback="fb") == "fb"
    assert _loggable_id(None) == "-"
    assert _loggable_id("") == "-"
    assert _loggable_id("   ") == "-"
    assert _loggable_id(42) == "42"
    # Legitimate ids survive untouched.
    assert _loggable_id("sess-9_a.b:c") == "sess-9_a.b:c"


def test_chat_stream_open_failure_is_logged_with_timings(streaming_client, caplog):
    caplog.set_level(logging.INFO)
    client = streaming_client(chunks=(), open_error=RuntimeError("upstream refused"))

    response = client.post(
        "/a/agent-1/api/chat/stream",
        content=b'{"message":"hello","session_id":"sess-9"}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 502
    lines = _records(caplog, "Runtime stream open failed")
    assert len(lines) == 1
    assert "agent_id=agent-1" in lines[0]
    assert "session_id=sess-9" in lines[0]
    assert "exception_class=RuntimeError" in lines[0]
    assert float(_field(lines[0], "resolve_ms")) >= 0
    assert _records(caplog, "Runtime stream end") == []


def test_upstream_agent_url_resolution_is_logged_at_info(caplog):
    from app.services.proxy_service import ProxyService

    caplog.set_level(logging.INFO, logger="app.services.proxy_service")
    base_url = ProxyService().build_agent_base_url(
        SimpleNamespace(id="agent-1", service_name="agent-svc", namespace="efp-agents")
    )

    assert base_url == "http://agent-svc.efp-agents.svc.cluster.local:8000"
    records = _matching_records(caplog, "Resolved runtime base URL")
    assert len(records) == 1
    assert records[0].getMessage().startswith("Resolved runtime base URL")
    assert f"base_url={base_url}" in records[0].getMessage()
    assert records[0].levelno == logging.INFO


def _dockerfile_uvicorn_argv(env: dict[str, str] | None = None) -> list[str]:
    """The argv the container really execs, with ``${VAR:-default}`` expanded."""
    source = Path("Dockerfile").read_text(encoding="utf-8")
    cmd_lines = [line for line in source.splitlines() if line.startswith("CMD ")]
    assert cmd_lines, "Dockerfile has no CMD"
    shell_command = json.loads(cmd_lines[-1][len("CMD "):])[-1]
    _, _, exec_command = shell_command.rpartition("exec ")
    expanded = re.sub(
        r"\$\{(\w+):-([^}]*)\}",
        lambda match: (env or {}).get(match.group(1)) or match.group(2),
        exec_command,
    )
    argv = shlex.split(expanded)
    assert argv[0] == "uvicorn", argv
    return argv[1:]


def _proxy_headers_client_addr(trusted_hosts) -> str:
    """Run one request through the middleware uvicorn installs for --proxy-headers."""
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    seen = {}

    async def _app(scope, receive, send):
        seen["client"] = scope["client"]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _send(_message):
        return None

    scope = {
        "type": "http",
        "scheme": "http",
        # The ingress-nginx pod, i.e. what uvicorn sees as the peer.
        "client": ("10.42.0.7", 51234),
        "headers": [(b"x-forwarded-for", b"203.0.113.9"), (b"x-forwarded-proto", b"https")],
    }
    asyncio.run(ProxyHeadersMiddleware(_app, trusted_hosts=trusted_hosts)(scope, _receive, _send))
    return seen["client"][0]


def _dockerfile_uvicorn_config(env: dict[str, str] | None = None):
    """Resolve the deployed argv into the uvicorn Config the server would run."""
    from uvicorn.config import Config
    from uvicorn.main import main as uvicorn_cli

    params = uvicorn_cli.make_context("uvicorn", _dockerfile_uvicorn_argv(env)).params
    return Config(
        app="app.main:app",
        proxy_headers=params["proxy_headers"],
        forwarded_allow_ips=params["forwarded_allow_ips"],
    )


def test_uvicorn_trusts_ingress_forwarded_headers_for_access_log_client_addr(monkeypatch):
    from uvicorn.config import Config

    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    config = _dockerfile_uvicorn_config()

    assert config.proxy_headers is True
    # The behaviour that matters: under the deployed argv the ingress pod is a
    # trusted peer, so the access log resolves the real browser IP.
    assert _proxy_headers_client_addr(config.forwarded_allow_ips) == "203.0.113.9"
    # Without the flag uvicorn trusts loopback only and logs the ingress pod IP.
    assert _proxy_headers_client_addr(Config(app="app.main:app").forwarded_allow_ips) == "10.42.0.7"


def test_dockerfile_forwarded_allow_ips_is_overridable_by_environment(monkeypatch):
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)

    trusting_ingress = _dockerfile_uvicorn_config(env={"FORWARDED_ALLOW_IPS": "10.42.0.7"})
    assert _proxy_headers_client_addr(trusting_ingress.forwarded_allow_ips) == "203.0.113.9"

    # A deployment without a proxy in front can pin the trusted set down, which
    # only works because the argv reads the env var instead of hardcoding '*'.
    trusting_someone_else = _dockerfile_uvicorn_config(env={"FORWARDED_ALLOW_IPS": "192.0.2.1"})
    assert _proxy_headers_client_addr(trusting_someone_else.forwarded_allow_ips) == "10.42.0.7"
