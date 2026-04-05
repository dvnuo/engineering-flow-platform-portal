from fastapi.testclient import TestClient
from uuid import uuid4

from app.main import app
from app.db import SessionLocal
from app.models.agent import Agent
from app.models.user import User
from app.services.auth_service import issue_session_token
import app.api.proxy as proxy_module


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

    def __call__(self, url, *args, **kwargs):
        self.url = url
        return self

    async def __aenter__(self):
        return self.upstream

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _create_running_agent(owner_user_id: int):
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

    with TestClient(app) as client:
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

    with TestClient(app) as client:
        user_id = _create_active_user()
        agent_id = _create_running_agent(user_id)
        token = issue_session_token(user_id)

        with client.websocket_connect(f"/a/{agent_id}/api/events?token={token}&stream=runtime") as ws:
            assert ws.receive_text() == '{"type":"iteration_start","data":{"iteration":1}}'

    assert fake_connect.url == "ws://runtime.local:8000/api/events?stream=runtime"
