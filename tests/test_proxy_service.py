"""Tests for proxy_service."""
from types import SimpleNamespace

import asyncio

from app.services.proxy_service import (
    ProxyService,
    build_portal_identity_fields,
    build_portal_identity_headers,
    sanitize_header_value,
)


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
        "X-Portal-User-Id": "42",
        "X-Portal-User-Name": "Taylor",
    }


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
