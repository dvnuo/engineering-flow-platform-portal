from fastapi.testclient import TestClient
from uuid import uuid4

from app.main import app
from app.db import SessionLocal
from app.models.agent import Agent
from app.models.user import User
from app.services.auth_service import issue_session_token
import app.api.proxy as proxy_module


class _FakeUpstream:
    def __init__(self, message: str):
        self._message = message
        self._sent = False
        self.sent_messages = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._sent:
            self._sent = True
            return self._message
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


def test_ws_proxy_events_passthrough(monkeypatch):
    monkeypatch.setattr(
        proxy_module.proxy_service,
        "build_agent_base_url",
        lambda _agent: "http://runtime.local:8000",
    )

    upstream = _FakeUpstream('{"type":"connected","message":"Connected to EFP event bus"}')
    fake_connect = _FakeConnect(upstream)
    monkeypatch.setattr(proxy_module.websockets, "connect", fake_connect)

    with TestClient(app) as client:
        db = SessionLocal()
        user = User(username=f"ws-user-{uuid4().hex[:8]}", password_hash="x", role="user", is_active=True)
        db.add(user)
        db.commit()
        db.refresh(user)

        agent = Agent(
            name=f"ws-agent-{uuid4().hex[:6]}",
            owner_user_id=user.id,
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
        user_id = user.id
        agent_id = agent.id
        db.close()

        token = issue_session_token(user_id)

        with client.websocket_connect(
            f"/a/{agent_id}/api/events",
            cookies={"portal_session": token},
        ) as ws:
            payload = ws.receive_text()
            assert payload == '{"type":"connected","message":"Connected to EFP event bus"}'

    assert fake_connect.url == "ws://runtime.local:8000/api/events"
