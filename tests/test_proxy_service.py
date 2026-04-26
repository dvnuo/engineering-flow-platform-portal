"""Tests for proxy_service."""
from types import SimpleNamespace
from pathlib import Path
import sys

import asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.services.proxy_service as proxy_module
from app.services.proxy_service import (
    ProxyService,
    build_portal_agent_identity_headers,
    build_portal_identity_fields,
    build_portal_identity_headers,
    sanitize_header_value,
)


def test_proxy_service_init():
    service = ProxyService()
    assert service is not None


def test_proxy_service_init_without_kubernetes_dependency(monkeypatch):
    service = ProxyService()
    monkeypatch.setattr(proxy_module, "k8s_client", None)
    monkeypatch.setattr(proxy_module, "k8s_config", None)
    monkeypatch.setattr(proxy_module, "get_settings", lambda: SimpleNamespace(k8s_enabled=True))

    assert service.core_api is None


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


def test_proxy_service_forward_includes_safe_extra_headers(monkeypatch):
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
    asyncio.run(service.forward(
        agent=agent,
        method="POST",
        subpath="api/chat",
        query_items=[],
        body=b"{}",
        headers={"content-type": "application/json"},
        extra_headers={
            "x-portal-user-id": "42",
            "x-portal-user-name": "Taylor 😀",
            "X-Portal-Agent-Name": "Agent One",
            "X-Portal-Author-Source": "portal",
            "Content-Type": "text/plain",
            "Host": "evil.example",
            "Connection": "close",
            "X-Unsafe-Header": "should-not-pass",
        },
    ))

    assert captured["headers"] == {
        "content-type": "application/json",
        "X-Portal-Author-Source": "portal",
        "X-Portal-Agent-Name": "Agent One",
        "X-Portal-User-Id": "42",
        "X-Portal-User-Name": "Taylor",
    }


def test_proxy_service_forward_can_return_content_disposition_header(monkeypatch):
    service = ProxyService()
    monkeypatch.setattr(service, "build_agent_base_url", lambda _agent: "http://runtime.local:8000")

    class _Resp:
        status_code = 200
        content = b"# notes"
        headers = {
            "content-type": "text/markdown",
            "content-disposition": 'attachment; filename="notes.md"',
            "set-cookie": "should-not-pass",
        }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, **kwargs):
            _ = kwargs
            return _Resp()

    monkeypatch.setattr("app.services.proxy_service.httpx.AsyncClient", lambda timeout=None: _Client())

    agent = SimpleNamespace(service_name="svc", namespace="efp-agents")
    result = asyncio.run(service.forward(
        agent=agent,
        method="GET",
        subpath="api/server-files/download",
        query_items=[("paths", "/workspace/notes.md")],
        body=None,
        headers={},
        return_response_headers=True,
    ))

    assert len(result) == 4
    status_code, content, content_type, response_headers = result
    assert status_code == 200
    assert content == b"# notes"
    assert content_type == "text/markdown"
    assert response_headers == {"Content-Disposition": 'attachment; filename="notes.md"'}
    assert "set-cookie" not in response_headers


def test_proxy_service_forward_drops_unsafe_content_disposition(monkeypatch):
    service = ProxyService()
    monkeypatch.setattr(service, "build_agent_base_url", lambda _agent: "http://runtime.local:8000")

    class _Resp:
        status_code = 200
        content = b"unsafe"
        headers = {
            "content-type": "text/plain",
            "content-disposition": 'attachment; filename="safe.txt"\r\nX-Evil: 1',
        }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, **kwargs):
            _ = kwargs
            return _Resp()

    monkeypatch.setattr("app.services.proxy_service.httpx.AsyncClient", lambda timeout=None: _Client())

    agent = SimpleNamespace(service_name="svc", namespace="efp-agents")
    status_code, content, content_type, response_headers = asyncio.run(service.forward(
        agent=agent,
        method="GET",
        subpath="api/server-files/download",
        query_items=[],
        body=None,
        headers={},
        return_response_headers=True,
    ))

    assert status_code == 200
    assert content == b"unsafe"
    assert content_type == "text/plain"
    assert response_headers == {}


def test_proxy_service_forward_base_url_error_returns_three_tuple_by_default(monkeypatch):
    service = ProxyService()

    def _raise_base_url(_agent):
        raise ValueError("Cannot determine node IP")

    monkeypatch.setattr(service, "build_agent_base_url", _raise_base_url)

    agent = SimpleNamespace(service_name="svc", namespace="efp-agents")
    result = asyncio.run(service.forward(
        agent=agent,
        method="GET",
        subpath="api/server-files/download",
        query_items=[],
        body=None,
        headers={},
    ))

    assert len(result) == 3
    status_code, content, content_type = result
    assert status_code == 502
    assert content_type == "text/plain"
    assert b"Cannot determine node IP" in content


def test_proxy_service_forward_base_url_error_returns_four_tuple_when_headers_requested(monkeypatch):
    service = ProxyService()

    def _raise_base_url(_agent):
        raise ValueError("Cannot determine node IP")

    monkeypatch.setattr(service, "build_agent_base_url", _raise_base_url)

    agent = SimpleNamespace(service_name="svc", namespace="efp-agents")
    result = asyncio.run(service.forward(
        agent=agent,
        method="GET",
        subpath="api/server-files/download",
        query_items=[],
        body=None,
        headers={},
        return_response_headers=True,
    ))

    assert len(result) == 4
    status_code, content, content_type, response_headers = result
    assert status_code == 502
    assert content_type == "text/plain"
    assert response_headers == {}
    assert b"Cannot determine node IP" in content


def test_build_portal_identity_headers_sanitizes_and_omits_blank_name():
    user = SimpleNamespace(
        id=7,
        username="fallback-user",
        nickname="  Eve\r\n\x00\tUser  ",
    )

    headers = build_portal_identity_headers(user)

    assert headers["X-Portal-Author-Source"] == "portal"
    assert headers["X-Portal-User-Id"] == "7"
    assert headers["X-Portal-User-Name"] == "EveUser"


def test_build_portal_identity_headers_omits_empty_identity_fields():
    user = SimpleNamespace(
        id=None,
        username="   \n\r\t ",
        nickname=None,
    )

    headers = build_portal_identity_headers(user)

    assert headers == {"X-Portal-Author-Source": "portal"}


def test_build_portal_identity_headers_falls_back_to_sanitized_username():
    user = SimpleNamespace(
        id="123",
        nickname=" \r\n\t ",
        username="alice",
    )

    headers = build_portal_identity_headers(user)

    assert headers == {
        "X-Portal-Author-Source": "portal",
        "X-Portal-User-Id": "123",
        "X-Portal-User-Name": "alice",
    }


def test_build_portal_agent_identity_headers_adds_sanitized_agent_name():
    user = SimpleNamespace(id=123, username="alice", nickname="Alice")
    agent = SimpleNamespace(name=" Agent\r\nName ")

    headers = build_portal_agent_identity_headers(user, agent)

    assert headers == {
        "X-Portal-Author-Source": "portal",
        "X-Portal-User-Id": "123",
        "X-Portal-User-Name": "Alice",
        "X-Portal-Agent-Name": "AgentName",
    }


def test_build_portal_agent_identity_headers_omits_empty_agent_name():
    user = SimpleNamespace(id=123, username="alice", nickname="Alice")
    agent = SimpleNamespace(name=" \r\n\t ")

    headers = build_portal_agent_identity_headers(user, agent)

    assert headers == {
        "X-Portal-Author-Source": "portal",
        "X-Portal-User-Id": "123",
        "X-Portal-User-Name": "Alice",
    }


def test_build_portal_identity_fields_normalizes_identity_values():
    user = SimpleNamespace(id="123", nickname=" Alice\r\n", username="ignored")

    assert build_portal_identity_fields(user) == {"user_id": "123", "user_name": "Alice"}


def test_build_portal_identity_fields_falls_back_to_sanitized_username():
    user = SimpleNamespace(id="123", nickname=" \r\n\t ", username="alice")

    assert build_portal_identity_fields(user) == {"user_id": "123", "user_name": "alice"}


def test_build_portal_identity_fields_omits_empty_values():
    user = SimpleNamespace(id=" \r\n\t ", nickname=None, username=" \r\n\t ")

    assert build_portal_identity_fields(user) == {}


def test_proxy_service_forward_multipart_includes_safe_extra_headers(monkeypatch):
    service = ProxyService()
    monkeypatch.setattr(service, "build_agent_base_url", lambda _agent: "http://runtime.local:8000")

    captured = {}

    class _Resp:
        status_code = 201
        content = b'{"ok": true}'
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
    status_code, _, _ = asyncio.run(service.forward_multipart(
        agent=agent,
        method="POST",
        subpath="api/files/upload",
        query_items=[],
        files={"file": ("a.txt", b"hi", "text/plain")},
        headers={},
        extra_headers={"X-Portal-Author-Source": "portal", "X-Portal-User-Id": "9"},
    ))

    assert status_code == 201
    assert captured["url"] == "http://runtime.local:8000/api/files/upload"
    assert captured["files"]["file"][0] == "a.txt"
    assert captured["headers"] == {"X-Portal-Author-Source": "portal", "X-Portal-User-Id": "9"}


def test_sanitize_header_value_preserves_zero_and_strips_controls():
    assert sanitize_header_value(0) == "0"
    assert sanitize_header_value(None) == ""
    assert sanitize_header_value("  A\r\n\tB\x00  ") == "AB"
    assert sanitize_header_value("José 😀") == "José"
    assert sanitize_header_value("😀😀") == ""
