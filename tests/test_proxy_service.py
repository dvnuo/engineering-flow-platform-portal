"""Tests for proxy_service."""
from types import SimpleNamespace

import asyncio

from app.services.proxy_service import ProxyService


def test_proxy_service_init():
    service = ProxyService()
    assert service is not None


def test_proxy_service_build_url_no_k8s():
    service = ProxyService()
    assert hasattr(service, 'build_agent_base_url')


def test_proxy_service_forward_root_path_unchanged(monkeypatch):
    service = ProxyService()
    monkeypatch.setattr(service, "build_agent_base_url", lambda _agent: "http://runtime.local:8000")

    captured = {}

    class _Resp:
        status_code = 200
        content = b"ok"
        headers = {"content-type": "application/json"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, **kwargs):
            captured.update(kwargs)
            return _Resp()

    monkeypatch.setattr("app.services.proxy_service.httpx.AsyncClient", lambda timeout=None: _Client())

    agent = SimpleNamespace(service_name="svc", namespace="efp-agents")
    status_code, content, content_type = asyncio.run(service.forward(
        agent=agent,
        method="GET",
        subpath="",
        query_items=[("q", "1")],
        body=None,
        headers={},
    ))

    assert status_code == 200
    assert content == b"ok"
    assert content_type == "application/json"
    assert captured["url"] == "http://runtime.local:8000/"


def test_proxy_service_forward_subpath_unchanged(monkeypatch):
    service = ProxyService()
    monkeypatch.setattr(service, "build_agent_base_url", lambda _agent: "http://runtime.local:8000")

    captured = {}

    class _Resp:
        status_code = 202
        content = b"accepted"
        headers = {"content-type": "text/plain"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, **kwargs):
            captured.update(kwargs)
            return _Resp()

    monkeypatch.setattr("app.services.proxy_service.httpx.AsyncClient", lambda timeout=None: _Client())

    agent = SimpleNamespace(service_name="svc", namespace="efp-agents")
    asyncio.run(service.forward(
        agent=agent,
        method="POST",
        subpath="api/events",
        query_items=[("stream", "runtime")],
        body=b"{}",
        headers={"content-type": "application/json"},
    ))

    assert captured["url"] == "http://runtime.local:8000/api/events"
    assert captured["params"] == [("stream", "runtime")]
    assert captured["headers"] == {"content-type": "application/json"}
