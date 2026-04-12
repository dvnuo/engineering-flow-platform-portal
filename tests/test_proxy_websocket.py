from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect
from uuid import uuid4

from app.main import app
from app.db import Base, SessionLocal, engine
from app.models.agent import Agent
from app.models.user import User
from app.services.auth_service import issue_session_token
import app.log_context as log_context_module
import app.api.proxy as proxy_module


def _ensure_tables():
    Base.metadata.create_all(bind=engine)


class _FakeUpstream:
    def __init__(self, messages):
        self._messages = list(messages)
        self.sent_messages = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._messages:
            return self._messages.pop(0)
        raise StopAsyncIteration

    async def send(self, payload):
        self.sent_messages.append(payload)


class _FakeConnect:
    def __init__(self, upstream):
        self.upstream = upstream
        self.url = None
        self.kwargs = {}

    def __call__(self, url, *args, **kwargs):
        self.url = url
        self.kwargs = kwargs
        return self

    async def __aenter__(self):
        return self.upstream

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _create_running_agent(owner_user_id: int):
    _ensure_tables()
    db = SessionLocal()
    try:
        agent = Agent(
            name=f"ws-agent-{uuid4().hex[:6]}",
            owner_user_id=owner_user_id,
            visibility="private",
            status="running",
            image="ghcr.io/test/image:latest",
            deployment_name="d",
            service_name="svc",
            pvc_name="pvc",
            namespace="efp-agents",
            disk_size_gi=20,
            mount_path="/data",
        )
        db.add(agent)
        db.commit()
        db.refresh(agent)
        return agent.id
    finally:
        db.close()


def _create_active_user():
    _ensure_tables()
    db = SessionLocal()
    try:
        user = User(username=f"ws-user-{uuid4().hex[:8]}", nickname=None, password_hash="x", role="user", is_active=True)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user.id
    finally:
        db.close()


def test_ws_proxy_events_passthrough_old_and_normalized_payloads(monkeypatch):
    monkeypatch.setattr(
        proxy_module.proxy_service,
        "build_agent_base_url",
        lambda _agent: "http://runtime.local:8000",
    )

    upstream = _FakeUpstream([
        '{"type":"connected","message":"Connected to EFP event bus"}',
        '{"event_type":"tool_result","state":"running","session_id":"s1","request_id":"r1","agent_id":"a1","summary":"Tool completed","detail_payload":{"tool":"search"},"created_at":"2026-04-04T00:00:00Z"}',
    ])
    fake_connect = _FakeConnect(upstream)
    monkeypatch.setattr(proxy_module.websockets, "connect", fake_connect)

    client = TestClient(app)
    user_id = _create_active_user()
    agent_id = _create_running_agent(user_id)
    token = issue_session_token(user_id)

    with client.websocket_connect(
        f"/a/{agent_id}/api/events",
        cookies={"portal_session": token},
    ) as ws:
        assert ws.receive_text() == '{"type":"connected","message":"Connected to EFP event bus"}'
        assert ws.receive_text() == '{"event_type":"tool_result","state":"running","session_id":"s1","request_id":"r1","agent_id":"a1","summary":"Tool completed","detail_payload":{"tool":"search"},"created_at":"2026-04-04T00:00:00Z"}'

    assert fake_connect.url == "ws://runtime.local:8000/api/events"


def test_ws_proxy_events_query_token_auth_and_no_token_forward(monkeypatch):
    monkeypatch.setattr(
        proxy_module.proxy_service,
        "build_agent_base_url",
        lambda _agent: "http://runtime.local:8000",
    )

    upstream = _FakeUpstream(['{"type":"iteration_start","data":{"iteration":1}}'])
    fake_connect = _FakeConnect(upstream)
    monkeypatch.setattr(proxy_module.websockets, "connect", fake_connect)

    client = TestClient(app)
    user_id = _create_active_user()
    agent_id = _create_running_agent(user_id)
    token = issue_session_token(user_id)

    with client.websocket_connect(f"/a/{agent_id}/api/events?token={token}&Token=extra&TOKEN=extra2&stream=runtime") as ws:
        assert ws.receive_text() == '{"type":"iteration_start","data":{"iteration":1}}'

    assert fake_connect.url == "ws://runtime.local:8000/api/events?stream=runtime"


def test_ws_proxy_events_forwards_session_id_and_strips_tokens(monkeypatch):
    monkeypatch.setattr(
        proxy_module.proxy_service,
        "build_agent_base_url",
        lambda _agent: "http://runtime.local:8000",
    )

    upstream = _FakeUpstream(['{"type":"iteration_start","data":{"iteration":1}}'])
    fake_connect = _FakeConnect(upstream)
    monkeypatch.setattr(proxy_module.websockets, "connect", fake_connect)

    client = TestClient(app)
    user_id = _create_active_user()
    agent_id = _create_running_agent(user_id)
    token = issue_session_token(user_id)

    with client.websocket_connect(
        f"/a/{agent_id}/api/events?token={token}&session_id=sess-42&Token=extra"
    ) as ws:
        assert ws.receive_text() == '{"type":"iteration_start","data":{"iteration":1}}'

    assert fake_connect.url == "ws://runtime.local:8000/api/events?session_id=sess-42"


def test_ws_proxy_runtime_base_url_resolution_failure_closes_cleanly(monkeypatch):
    monkeypatch.setattr(
        proxy_module.proxy_service,
        "build_agent_base_url",
        lambda _agent: (_ for _ in ()).throw(ValueError("no runtime url")),
    )

    client = TestClient(app)
    user_id = _create_active_user()
    agent_id = _create_running_agent(user_id)
    token = issue_session_token(user_id)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            f"/a/{agent_id}/api/events",
            cookies={"portal_session": token},
        ) as ws:
            ws.receive_text()
    assert exc_info.value.code == 1011


def test_ws_proxy_events_forwards_trace_headers_to_runtime(monkeypatch):
    monkeypatch.setattr(
        proxy_module.proxy_service,
        "build_agent_base_url",
        lambda _agent: "http://runtime.local:8000",
    )
    monkeypatch.setattr(proxy_module, "generate_span_id", lambda: "span-ws-1")

    upstream = _FakeUpstream(['{"type":"connected"}'])
    fake_connect = _FakeConnect(upstream)
    monkeypatch.setattr(proxy_module.websockets, "connect", fake_connect)

    client = TestClient(app)
    user_id = _create_active_user()
    agent_id = _create_running_agent(user_id)
    token = issue_session_token(user_id)

    with client.websocket_connect(
        f"/a/{agent_id}/api/events",
        cookies={"portal_session": token},
        headers={"X-Trace-Id": "trace-ws-1"},
    ) as ws:
        assert ws.receive_text() == '{"type":"connected"}'

    assert fake_connect.url == "ws://runtime.local:8000/api/events"
    assert fake_connect.kwargs["additional_headers"]["X-Trace-Id"] == "trace-ws-1"
    assert fake_connect.kwargs["additional_headers"]["X-Span-Id"] == "span-ws-1"
    assert "-" not in fake_connect.kwargs["additional_headers"].values()


def test_ws_proxy_events_clears_portal_task_and_dispatch_fields_for_entry_context(monkeypatch):
    monkeypatch.setattr(
        proxy_module.proxy_service,
        "build_agent_base_url",
        lambda _agent: "http://runtime.local:8000",
    )
    monkeypatch.setattr(proxy_module, "generate_span_id", lambda: "span-ws-clean-1")

    captured_bind_kwargs = {}
    original_bind_log_context = log_context_module.bind_log_context

    def _bind_log_context_wrapper(**kwargs):
        captured_bind_kwargs.update(kwargs)
        return original_bind_log_context(**kwargs)

    monkeypatch.setattr(proxy_module, "bind_log_context", _bind_log_context_wrapper)

    upstream = _FakeUpstream(['{"type":"connected"}'])
    fake_connect = _FakeConnect(upstream)
    monkeypatch.setattr(proxy_module.websockets, "connect", fake_connect)

    client = TestClient(app)
    user_id = _create_active_user()
    agent_id = _create_running_agent(user_id)
    token = issue_session_token(user_id)

    with client.websocket_connect(
        f"/a/{agent_id}/api/events",
        cookies={"portal_session": token},
        headers={"X-Trace-Id": "trace-ws-clean-1"},
    ) as ws:
        assert ws.receive_text() == '{"type":"connected"}'

    assert captured_bind_kwargs["portal_task_id"] == "-"
    assert captured_bind_kwargs["portal_dispatch_id"] == "-"
    assert captured_bind_kwargs["trace_id"] == "trace-ws-clean-1"
    assert captured_bind_kwargs["span_id"] == "span-ws-clean-1"

    assert fake_connect.kwargs["additional_headers"]["X-Trace-Id"] == "trace-ws-clean-1"
    assert fake_connect.kwargs["additional_headers"]["X-Span-Id"] == "span-ws-clean-1"
    assert "X-Parent-Span-Id" not in fake_connect.kwargs["additional_headers"]
    assert "X-Portal-Task-Id" not in fake_connect.kwargs["additional_headers"]
    assert "X-Portal-Dispatch-Id" not in fake_connect.kwargs["additional_headers"]
