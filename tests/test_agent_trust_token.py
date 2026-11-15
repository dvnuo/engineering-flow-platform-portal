"""Every request the Portal makes to an assistant's runtime carries that assistant's trust token.

A runtime whose pod has ``EFP_PORTAL_TRUST_TOKEN`` rejects "Portal" requests
without the matching ``X-Portal-Trust-Token`` header, so a call site that
forgets it is an outage for that feature. The tests below go through each
path the Portal reaches a runtime by, and the last one fails when a new
module starts building runtime URLs without the token.
"""

import asyncio
import hashlib
import hmac
from pathlib import Path
import re
from types import SimpleNamespace
import uuid

import pytest
from fastapi.testclient import TestClient

from app.services import agent_trust
from app.services.agent_trust import (
    TRUST_TOKEN_ENV,
    TRUST_TOKEN_HEADER,
    agent_trust_headers,
    agent_trust_token,
    trust_token_matches,
)
from app.services.proxy_service import ProxyService, build_portal_agent_headers

REPO = Path(__file__).resolve().parents[1]
KEY = "unit-test-secret-key"


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "secret_key", KEY)
    return KEY


def _expected(agent_id: str, key: str = KEY) -> str:
    return hmac.new(key.encode("utf-8"), ("agent-trust:v1:" + agent_id).encode("utf-8"), hashlib.sha256).hexdigest()


# --- the token ------------------------------------------------------------------


def test_names_are_the_ones_the_runtimes_agree_on():
    assert TRUST_TOKEN_HEADER == "X-Portal-Trust-Token"
    assert TRUST_TOKEN_ENV == "EFP_PORTAL_TRUST_TOKEN"


def test_token_is_hmac_sha256_of_the_versioned_agent_id_under_the_secret_key():
    token = agent_trust_token("agent-1")
    assert token == _expected("agent-1")
    assert re.fullmatch(r"[0-9a-f]{64}", token)
    # A fixed vector (also what `openssl dgst -sha256 -hmac unit-test-secret-key`
    # prints for the message), so a change to the derivation cannot pass
    # unnoticed: the runtimes compare against what is already in their pods.
    assert token == "aed8167898d27d431ae9477e4229bb8767dba66ba0211152bb1e12d944c5e1d4"
    assert agent_trust_token("agent-1") == agent_trust_token("agent-1")


def test_token_differs_per_assistant_and_per_key(monkeypatch):
    from app.config import get_settings

    first = agent_trust_token("agent-1")
    assert agent_trust_token("agent-2") != first
    assert agent_trust_token("agent-1 ") != first
    monkeypatch.setattr(get_settings(), "secret_key", "another-key")
    assert agent_trust_token("agent-1") != first
    assert agent_trust_token("agent-1") == _expected("agent-1", "another-key")


def test_token_reads_the_key_at_call_time_not_at_import():
    assert "get_settings()" in Path(agent_trust.__file__).read_text(encoding="utf-8")
    assert not hasattr(agent_trust, "settings")


def test_trust_token_matches():
    token = agent_trust_token("agent-1")
    assert trust_token_matches("agent-1", token) is True
    assert trust_token_matches("agent-1", f"  {token} ") is True
    assert trust_token_matches("agent-2", token) is False
    assert trust_token_matches("agent-1", token[:-1] + ("0" if token[-1] != "0" else "1")) is False
    assert trust_token_matches("agent-1", "") is False
    assert trust_token_matches("agent-1", None) is False
    assert trust_token_matches(None, token) is False
    assert trust_token_matches("agent-1", token.upper()) is False


def test_agent_trust_headers_take_an_assistant_or_its_id():
    expected = {"X-Portal-Trust-Token": _expected("agent-1")}
    assert agent_trust_headers(SimpleNamespace(id="agent-1", name="A")) == expected
    assert agent_trust_headers("agent-1") == expected
    agent_uuid = uuid.uuid4()
    assert agent_trust_headers(agent_uuid) == {"X-Portal-Trust-Token": _expected(str(agent_uuid))}
    assert agent_trust_headers(7) == {"X-Portal-Trust-Token": _expected("7")}


@pytest.mark.parametrize("agent", [None, "", "  ", SimpleNamespace(name="no id"), SimpleNamespace(id=None), SimpleNamespace(id="")])
def test_agent_trust_headers_are_empty_without_an_id(agent):
    assert agent_trust_headers(agent) == {}


# --- the header dict and the allowlist ----------------------------------------------


def test_portal_agent_headers_carry_the_token():
    user = SimpleNamespace(id=5, username="member", nickname="Member")
    headers = build_portal_agent_headers(user, SimpleNamespace(id="agent-1", name="Agent One"))
    assert headers["X-Portal-Trust-Token"] == _expected("agent-1")
    assert headers["X-Portal-Author-Source"] == "portal"


def test_outbound_header_allowlist_lets_the_token_through():
    outbound = ProxyService._build_outbound_headers(
        {"content-type": "application/json"},
        {"x-portal-trust-token": "abc123", "X-Not-Allowed": "nope"},
    )
    assert outbound == {"content-type": "application/json", "X-Portal-Trust-Token": "abc123"}


# --- ProxyService.forward / forward_multipart ------------------------------------------


class _Resp:
    status_code = 200
    content = b"{}"
    headers = {"content-type": "application/json"}


def _capturing_client(captured):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, **kwargs):
            captured.update(kwargs)
            return _Resp()

    return _Client


@pytest.mark.parametrize("extra_headers", [None, {}, {"X-Portal-Author-Source": "portal"}])
def test_forward_adds_the_token_whatever_the_caller_passes(monkeypatch, extra_headers):
    service = ProxyService()
    monkeypatch.setattr(service, "build_agent_base_url", lambda _agent: "http://runtime.local:8000")
    captured = {}
    monkeypatch.setattr("app.services.proxy_service.httpx.AsyncClient", _capturing_client(captured))

    agent = SimpleNamespace(id="agent-1", service_name="svc", namespace="efp-agents")
    asyncio.run(
        service.forward(agent=agent, method="GET", subpath="ready", query_items=[], body=None, headers={}, extra_headers=extra_headers)
    )
    assert captured["headers"]["X-Portal-Trust-Token"] == _expected("agent-1")


def test_forward_never_sends_another_assistants_token(monkeypatch):
    service = ProxyService()
    monkeypatch.setattr(service, "build_agent_base_url", lambda _agent: "http://runtime.local:8000")
    captured = {}
    monkeypatch.setattr("app.services.proxy_service.httpx.AsyncClient", _capturing_client(captured))

    agent = SimpleNamespace(id="agent-1", service_name="svc", namespace="efp-agents")
    other = build_portal_agent_headers(SimpleNamespace(id=5, username="m", nickname="M"), SimpleNamespace(id="agent-2", name="B"))
    asyncio.run(
        service.forward(agent=agent, method="GET", subpath="ready", query_items=[], body=None, headers={}, extra_headers=other)
    )
    assert captured["headers"]["X-Portal-Trust-Token"] == _expected("agent-1")


def test_forward_multipart_adds_the_token(monkeypatch):
    service = ProxyService()
    monkeypatch.setattr(service, "build_agent_base_url", lambda _agent: "http://runtime.local:8000")
    captured = {}
    monkeypatch.setattr("app.services.proxy_service.httpx.AsyncClient", _capturing_client(captured))

    agent = SimpleNamespace(id="agent-1", service_name="svc", namespace="efp-agents")
    asyncio.run(
        service.forward_multipart(
            agent=agent,
            method="POST",
            subpath="api/files/upload",
            query_items=[],
            files={"file": ("a.txt", b"hi", "text/plain")},
        )
    )
    assert captured["headers"] == {"X-Portal-Trust-Token": _expected("agent-1")}


# --- the user-facing proxy: JSON, SSE and downloads ---------------------------------------


@pytest.fixture
def proxied(monkeypatch):
    from app.main import app
    import app.api.proxy as proxy_module

    user = SimpleNamespace(id=88, username="owner", nickname="Owner", role="user")
    agent = SimpleNamespace(id="agent-1", owner_user_id=88, visibility="private", status="running", name="Agent One")
    app.dependency_overrides[proxy_module.get_current_user] = lambda: user
    app.dependency_overrides[proxy_module.get_db] = lambda: iter([object()])
    monkeypatch.setattr(proxy_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: agent))
    monkeypatch.setattr(proxy_module, "touch_agent_activity", lambda _agent_id: None)
    monkeypatch.setattr(proxy_module.proxy_service, "build_agent_base_url", lambda _agent: "http://runtime.local:8000")
    try:
        yield SimpleNamespace(client=TestClient(app), module=proxy_module)
    finally:
        app.dependency_overrides.clear()


def _streaming_client(captured, content_type):
    class _Upstream:
        status_code = 200
        headers = {"content-type": content_type}

        async def aiter_raw(self):
            yield b"chunk"

    class _Stream:
        async def __aenter__(self):
            return _Upstream()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def stream(self, **kwargs):
            captured.update(kwargs)
            return _Stream()

        async def aclose(self):
            return None

    return _Client


def test_proxy_json_request_carries_the_token(proxied, monkeypatch):
    captured = {}
    monkeypatch.setattr("app.services.proxy_service.httpx.AsyncClient", _capturing_client(captured))
    response = proxied.client.get("/a/agent-1/api/git-info")
    assert response.status_code == 200
    assert captured["url"] == "http://runtime.local:8000/api/git-info"
    assert captured["headers"]["X-Portal-Trust-Token"] == _expected("agent-1")


def test_proxy_sse_stream_carries_the_token(proxied, monkeypatch):
    captured = {}
    monkeypatch.setattr(proxied.module.httpx, "AsyncClient", _streaming_client(captured, "text/event-stream"))
    response = proxied.client.get("/a/agent-1/api/events/stream?session_id=s1")
    assert response.status_code == 200
    assert response.content == b"chunk"
    assert captured["url"] == "http://runtime.local:8000/api/events/stream"
    assert captured["headers"]["X-Portal-Trust-Token"] == _expected("agent-1")
    assert captured["headers"]["X-Portal-Author-Source"] == "portal"


def test_proxy_download_stream_carries_the_token(proxied, monkeypatch):
    captured = {}
    monkeypatch.setattr(proxied.module.httpx, "AsyncClient", _streaming_client(captured, "application/zip"))
    response = proxied.client.get("/a/agent-1/api/server-files/download?paths=output", headers={"Range": "bytes=0-9"})
    assert response.status_code == 200
    assert captured["url"] == "http://runtime.local:8000/api/server-files/download"
    assert captured["headers"]["X-Portal-Trust-Token"] == _expected("agent-1")
    assert captured["headers"]["Range"] == "bytes=0-9"


def test_a_token_header_sent_by_the_browser_is_not_what_reaches_the_runtime(proxied, monkeypatch):
    captured = {}
    monkeypatch.setattr("app.services.proxy_service.httpx.AsyncClient", _capturing_client(captured))
    response = proxied.client.get("/a/agent-1/api/git-info", headers={"X-Portal-Trust-Token": "from-the-browser"})
    assert response.status_code == 200
    assert captured["headers"]["X-Portal-Trust-Token"] == _expected("agent-1")


def test_the_token_never_comes_back_to_the_browser(proxied, monkeypatch):
    captured = {}
    monkeypatch.setattr("app.services.proxy_service.httpx.AsyncClient", _capturing_client(captured))
    response = proxied.client.get("/a/agent-1/api/git-info")
    assert _expected("agent-1") not in response.text
    assert not any(_expected("agent-1") in value for value in response.headers.values())


# --- task dispatch, status and cancel -----------------------------------------------------


def _dispatcher_client(captured):
    class _Stream:
        def __init__(self, response):
            self._response = response

        async def __aenter__(self):
            return self._response

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _StatusResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        request = None

        async def aiter_bytes(self):
            yield b"{}"

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            captured.append(("POST", url, kwargs.get("headers")))
            return _Resp()

        def stream(self, method, url, **kwargs):
            captured.append((method, url, kwargs.get("headers")))
            return _Stream(_StatusResponse())

    return _Client


def test_task_dispatcher_runtime_calls_carry_the_token(monkeypatch):
    import app.services.task_dispatcher as task_dispatcher_module
    from app.services.task_dispatcher import TaskDispatcherService

    captured = []
    monkeypatch.setattr(task_dispatcher_module.httpx, "AsyncClient", _dispatcher_client(captured))
    service = TaskDispatcherService()
    metadata = {"trace_id": "trace-1", "portal_task_id": "task-1"}

    asyncio.run(service._post_to_runtime("http://runtime/api/tasks/execute", {"metadata": metadata}, agent_id="agent-1"))
    asyncio.run(service._post_cancel_to_runtime("http://runtime/api/tasks/task-1/cancel", metadata, agent_id="agent-1"))
    asyncio.run(service._get_runtime_task_status("http://runtime/api/tasks/task-1", metadata, agent_id="agent-1"))

    assert [(method, url) for method, url, _headers in captured] == [
        ("POST", "http://runtime/api/tasks/execute"),
        ("POST", "http://runtime/api/tasks/task-1/cancel"),
        ("GET", "http://runtime/api/tasks/task-1"),
    ]
    for _method, _url, headers in captured:
        assert headers["X-Portal-Trust-Token"] == _expected("agent-1")
        assert headers["X-Trace-Id"] == "trace-1"


def test_task_dispatcher_runtime_calls_cannot_be_made_without_naming_the_assistant():
    from app.services.task_dispatcher import TaskDispatcherService

    service = TaskDispatcherService()
    for call in (
        lambda: service._post_to_runtime("http://runtime/x", {}),
        lambda: service._post_cancel_to_runtime("http://runtime/x", {}),
        lambda: service._get_runtime_task_status("http://runtime/x", {}),
    ):
        with pytest.raises(TypeError):
            call()


def test_task_dispatcher_passes_the_assistant_at_every_call_site():
    source = (REPO / "app/services/task_dispatcher.py").read_text(encoding="utf-8")
    calls = re.findall(r"await self\._(?:post_to_runtime|post_cancel_to_runtime|get_runtime_task_status)\(([^\n]*)\)", source)
    assert len(calls) == 4
    assert all("agent_id=" in call for call in calls)
    assert '"agent_id": agent.id,' in source


# --- the pod ---------------------------------------------------------------------------


def test_assistant_pod_receives_its_token():
    from app.services.k8s_service import K8sService

    service = K8sService()
    for runtime_type in ("native", "opencode"):
        agent = SimpleNamespace(id="agent-1", runtime_type=runtime_type, mount_path="/workspace", name="A")
        env = {item.name: getattr(item, "value", None) for item in service._build_agent_container_env(agent)}
        assert env["EFP_PORTAL_TRUST_TOKEN"] == _expected("agent-1")
        assert env["PORTAL_AGENT_ID"] == "agent-1"


def test_no_token_in_an_env_without_an_assistant():
    from app.services.k8s_service import K8sService

    names = {item.name for item in K8sService()._build_agent_container_env(None)}
    assert "EFP_PORTAL_TRUST_TOKEN" not in names


def test_deployment_patch_carries_the_token():
    from app.services.k8s_service import K8sService

    service = K8sService()
    captured = {}
    service._patch_agent_deployment_merge = lambda _agent, body: captured.update(body=body)
    agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=1,
        runtime_type="native",
        mount_path="/workspace",
        name="A",
        image="example/image:latest",
        deployment_name="dep",
        namespace="efp-agents",
        skill_repo_url=None,
        skill_branch=None,
        agent_settings_repo_url=None,
        agent_settings_branch=None,
        runtime_profile_id=None,
        cpu=None,
        memory=None,
    )
    service._patch_deployment(agent)
    container = captured["body"]["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item.get("value") for item in container["env"]}
    assert env["EFP_PORTAL_TRUST_TOKEN"] == _expected("agent-1")


# --- nothing reaches a runtime around the token ----------------------------------------------


def test_every_module_that_reaches_a_runtime_sends_the_token():
    """A new module that builds runtime URLs must send the token; add it here once it does.

    ``ProxyService.forward`` / ``forward_multipart`` add the token themselves,
    so their callers need nothing. Whoever calls ``build_agent_base_url`` and
    then opens its own connection does.
    """
    builds_runtime_urls = set()
    for path in sorted((REPO / "app").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "build_agent_base_url(" in text:
            builds_runtime_urls.add(path.relative_to(REPO).as_posix())
    assert builds_runtime_urls == {
        "app/api/proxy.py",
        "app/services/proxy_service.py",
        "app/services/runtime_capability_sync_service.py",
        "app/services/task_dispatcher.py",
    }
    for relative in builds_runtime_urls:
        assert "agent_trust_headers(" in (REPO / relative).read_text(encoding="utf-8"), relative

    proxy_api = (REPO / "app/api/proxy.py").read_text(encoding="utf-8")
    # Two streamed requests and one WebSocket handshake are opened by hand there.
    assert proxy_api.count("proxy_service._build_outbound_headers(forward_headers, extra_headers)") == 2
    assert proxy_api.count("outbound_headers.update(agent_trust_headers(agent))") == 2
    assert "**agent_trust_headers(agent)," in proxy_api

    proxy_service = (REPO / "app/services/proxy_service.py").read_text(encoding="utf-8")
    assert proxy_service.count("self._build_outbound_headers(") == 2
    assert proxy_service.count("outbound_headers.update(agent_trust_headers(agent))") == 2
