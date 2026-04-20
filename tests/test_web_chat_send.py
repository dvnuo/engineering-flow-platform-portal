import json
from types import SimpleNamespace

from fastapi.testclient import TestClient


def test_app_chat_send_forwards_identity_only_in_headers(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=123, username="alice", nickname=" Alice\r\n", role="user")
    fake_agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=123,
        visibility="private",
        status="running",
        name="Agent One",
    )

    class _DB:
        def close(self):
            return None

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )

    captured = {}

    async def _fake_forward(**kwargs):
        captured.update(kwargs)
        return 200, json.dumps({"response": "hello", "session_id": "s-1", "events": []}).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    monkeypatch.setattr(
        web_module.runtime_execution_context_service,
        "build_runtime_metadata",
        lambda _db, _agent: {
            "capability_profile_id": "cap-web",
            "policy_profile_id": "pol-web",
            "allowed_capability_ids": ["tool:shell"],
            "policy_context": {"policy_profile_id": "pol-web"},
            "governance_require_explicit_allow": True,
        },
    )

    client = TestClient(app)
    response = client.post(
        "/app/chat/send",
        data={
            "agent_id": "agent-1",
            "message": "hi",
            "session_id": "s-1",
            "attachments": json.dumps([{"id": "file-1"}]),
        },
    )

    assert response.status_code == 200

    forwarded_payload = json.loads(captured["body"].decode("utf-8"))
    assert forwarded_payload["message"] == "hi"
    assert forwarded_payload["session_id"] == "s-1"
    assert forwarded_payload["attachments"] == [{"id": "file-1"}]
    assert "portal_user_id" not in forwarded_payload
    assert "portal_user_name" not in forwarded_payload
    assert forwarded_payload["metadata"]["capability_profile_id"] == "cap-web"
    assert forwarded_payload["metadata"]["policy_profile_id"] == "pol-web"
    assert forwarded_payload["metadata"]["policy_context"]["policy_profile_id"] == "pol-web"

    assert captured["extra_headers"]["X-Portal-Author-Source"] == "portal"
    assert captured["extra_headers"]["X-Portal-User-Id"] == "123"
    assert captured["extra_headers"]["X-Portal-User-Name"] == "Alice"
    assert captured["extra_headers"]["X-Portal-Agent-Name"] == "Agent One"
    assert captured["headers"] == {"content-type": "application/json"}


def test_app_chat_send_drops_form_identity_and_uses_headers_only(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id="  456 \r\n", username="fallback-name", nickname="\tBob\r\n", role="user")
    fake_agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=999,
        visibility="public",
        status="running",
        name="Agent One",
    )

    class _DB:
        def close(self):
            return None

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )

    captured = {}

    async def _fake_forward(**kwargs):
        captured.update(kwargs)
        return 200, json.dumps({"response": "ok", "session_id": "s-2", "events": []}).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    monkeypatch.setattr(
        web_module.runtime_execution_context_service,
        "build_runtime_metadata",
        lambda _db, _agent: {
            "capability_profile_id": "cap-web",
            "policy_profile_id": "pol-web",
            "allowed_capability_ids": ["tool:shell"],
            "policy_context": {"policy_profile_id": "pol-web"},
        },
    )

    client = TestClient(app)
    response = client.post(
        "/app/chat/send",
        data={
            "agent_id": "agent-1",
            "message": "hello",
            "attachments": json.dumps([{"id": "file-2"}]),
            "portal_user_id": "spoofed",
            "portal_user_name": "spoofed",
        },
    )

    assert response.status_code == 200
    forwarded_payload = json.loads(captured["body"].decode("utf-8"))
    assert "portal_user_id" not in forwarded_payload
    assert "portal_user_name" not in forwarded_payload
    assert forwarded_payload["message"] == "hello"
    assert forwarded_payload["attachments"] == [{"id": "file-2"}]
    assert forwarded_payload["metadata"]["capability_profile_id"] == "cap-web"
    assert forwarded_payload["metadata"]["policy_profile_id"] == "pol-web"
    assert captured["extra_headers"]["X-Portal-User-Id"] == "456"
    assert captured["extra_headers"]["X-Portal-User-Name"] == "Bob"
    assert captured["extra_headers"]["X-Portal-Agent-Name"] == "Agent One"


def test_app_chat_send_succeeds_with_standard_portal_identity_headers_only(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=123, username="alice", nickname="Alice", role="user")
    fake_agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=123,
        visibility="private",
        status="running",
        name="Agent One",
    )

    class _DB:
        def close(self):
            return None

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )
    monkeypatch.setattr(
        web_module.runtime_execution_context_service,
        "build_runtime_metadata",
        lambda _db, _agent: {
            "capability_profile_id": "cap-web",
            "policy_profile_id": "pol-web",
        },
    )

    calls = {"count": 0}

    async def _fake_forward(**kwargs):
        calls["count"] += 1
        return 200, json.dumps({"response": "hello", "session_id": "s-1", "events": []}).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)

    client = TestClient(app)
    response = client.post(
        "/app/chat/send",
        data={
            "agent_id": "agent-1",
            "message": "hi",
        },
    )

    assert response.status_code == 200
    assert calls["count"] == 1


def test_app_chat_send_includes_display_blocks_attribute(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=123, username="alice", nickname="Alice", role="user")
    fake_agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=123,
        visibility="private",
        status="running",
        name="Agent One",
    )

    class _DB:
        def close(self):
            return None

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )
    monkeypatch.setattr(
        web_module.runtime_execution_context_service,
        "build_runtime_metadata",
        lambda _db, _agent: {"capability_profile_id": "cap-web", "policy_profile_id": "pol-web"},
    )

    async def _fake_forward(**_kwargs):
        payload = {
            "response": "hello",
            "display_blocks": [{"type": "markdown", "content": "hello"}],
            "session_id": "s-1",
            "events": [],
        }
        return 200, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)

    client = TestClient(app)
    response = client.post(
        "/app/chat/send",
        data={
            "agent_id": "agent-1",
            "message": "hi",
        },
    )

    assert response.status_code == 200
    assert "data-display-blocks=" in response.text


def test_app_chat_send_uses_content_when_response_missing(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=123, username="alice", nickname="Alice", role="user")
    fake_agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=123,
        visibility="private",
        status="running",
        name="Agent One",
    )

    class _DB:
        def close(self):
            return None

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )
    monkeypatch.setattr(
        web_module.runtime_execution_context_service,
        "build_runtime_metadata",
        lambda _db, _agent: {"capability_profile_id": "cap-web", "policy_profile_id": "pol-web"},
    )

    async def _fake_forward(**_kwargs):
        payload = {
            "content": "hello from content",
            "session_id": "s-legacy",
            "events": [],
        }
        return 200, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)

    client = TestClient(app)
    response = client.post(
        "/app/chat/send",
        data={
            "agent_id": "agent-1",
            "message": "hi",
        },
    )

    assert response.status_code == 200
    assert "(empty response)" not in response.text
    assert "hello from content" in response.text


def test_app_chat_send_does_not_emit_empty_response_placeholder_with_display_blocks(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=123, username="alice", nickname="Alice", role="user")
    fake_agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=123,
        visibility="private",
        status="running",
        name="Agent One",
    )

    class _DB:
        def close(self):
            return None

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )
    monkeypatch.setattr(
        web_module.runtime_execution_context_service,
        "build_runtime_metadata",
        lambda _db, _agent: {"capability_profile_id": "cap-web", "policy_profile_id": "pol-web"},
    )

    async def _fake_forward(**_kwargs):
        payload = {
            "response": "",
            "display_blocks": [{"type": "markdown", "content": "hello from block"}],
            "session_id": "s-block",
            "events": [],
        }
        return 200, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)

    client = TestClient(app)
    response = client.post(
        "/app/chat/send",
        data={
            "agent_id": "agent-1",
            "message": "hi",
        },
    )

    assert response.status_code == 200
    assert "data-display-blocks=" in response.text
    assert "(empty response)" not in response.text


def test_app_chat_send_normalizes_json_runtime_error(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=123, username="alice", nickname="Alice", role="user")
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=123, visibility="private", status="running", name="Agent One")

    class _DB:
        def close(self):
            return None

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent))
    monkeypatch.setattr(web_module.runtime_execution_context_service, "build_runtime_metadata", lambda _db, _agent: {})

    async def _fake_forward(**_kwargs):
        payload = {
            "error": {
                "message": "Model output was truncated because max_output_tokens was reached",
                "code": "max_output_tokens_exceeded",
                "details": {"incomplete_reason": "max_output_tokens"},
            }
        }
        return 500, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    client = TestClient(app)
    response = client.post("/app/chat/send", data={"agent_id": "agent-1", "message": "hi"})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "Runtime error:" in detail
    assert "Model output was truncated because max_output_tokens was reached" in detail
    assert "code=max_output_tokens_exceeded" in detail
    assert "incomplete_reason=max_output_tokens" in detail


def test_app_chat_send_runtime_error_non_json_is_bounded(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=123, username="alice", nickname="Alice", role="user")
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=123, visibility="private", status="running", name="Agent One")

    class _DB:
        def close(self):
            return None

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent))
    monkeypatch.setattr(web_module.runtime_execution_context_service, "build_runtime_metadata", lambda _db, _agent: {})

    raw = ("x" * 1500).encode("utf-8")

    async def _fake_forward(**_kwargs):
        return 400, raw, "text/plain"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    client = TestClient(app)
    response = client.post("/app/chat/send", data={"agent_id": "agent-1", "message": "hi"})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail.startswith("Runtime error: ")
    assert len(detail) <= 1020


def test_app_chat_send_runtime_error_hides_large_or_sensitive_fields(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=123, username="alice", nickname="Alice", role="user")
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=123, visibility="private", status="running", name="Agent One")

    class _DB:
        def close(self):
            return None

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent))
    monkeypatch.setattr(web_module.runtime_execution_context_service, "build_runtime_metadata", lambda _db, _agent: {})

    async def _fake_forward(**_kwargs):
        payload = {
            "error": {
                "message": "runtime failed",
                "code": "fatal",
                "details": {
                    "incomplete_reason": "max_output_tokens",
                    "prompt_budget_tokens": 32000,
                    "request_estimated_tokens": 34000,
                    "reserved_output_tokens": 4000,
                    "prompt": "SECRET_PROMPT_PAYLOAD",
                    "response": "VERY_LARGE_BODY",
                    "api_key": "SECRET_KEY",
                },
            }
        }
        return 500, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    client = TestClient(app)
    response = client.post("/app/chat/send", data={"agent_id": "agent-1", "message": "hi"})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "prompt_budget_tokens=32000" in detail
    assert "request_estimated_tokens=34000" in detail
    assert "reserved_output_tokens=4000" in detail
    assert "SECRET_PROMPT_PAYLOAD" not in detail
    assert "VERY_LARGE_BODY" not in detail
    assert "SECRET_KEY" not in detail


def test_app_chat_send_runtime_error_top_level_error_shape_includes_code_and_details(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=123, username="alice", nickname="Alice", role="user")
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=123, visibility="private", status="running", name="Agent One")

    class _DB:
        def close(self):
            return None

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent))
    monkeypatch.setattr(web_module.runtime_execution_context_service, "build_runtime_metadata", lambda _db, _agent: {})

    async def _fake_forward(**_kwargs):
        payload = {
            "error": "Model output was truncated because max_output_tokens was reached",
            "error_type": "truncated_response",
            "code": "max_output_tokens_exceeded",
            "details": {
                "incomplete_reason": "max_output_tokens",
                "request_estimated_tokens": 34000,
                "prompt_budget_tokens": 32000,
                "reserved_output_tokens": 16000,
                "request_over_budget": True,
                "max_prompt_tokens": 32000,
                "safety_margin_tokens": 1000,
                "max_output_tokens": 16000,
                "prompt": "SECRET_PROMPT",
                "api_key": "SECRET",
            },
        }
        return 500, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    client = TestClient(app)
    response = client.post("/app/chat/send", data={"agent_id": "agent-1", "message": "hi"})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "Runtime error:" in detail
    assert "Model output was truncated because max_output_tokens was reached" in detail
    assert "code=max_output_tokens_exceeded" in detail
    assert "incomplete_reason=max_output_tokens" in detail
    assert "request_estimated_tokens=34000" in detail
    assert "prompt_budget_tokens=32000" in detail
    assert "reserved_output_tokens=16000" in detail
    assert "request_over_budget=True" in detail
    assert "max_prompt_tokens=32000" in detail
    assert "safety_margin_tokens=1000" in detail
    assert "max_output_tokens=16000" in detail
    assert "SECRET_PROMPT" not in detail
    assert "SECRET" not in detail
    assert "prompt=" not in detail
    assert "api_key=" not in detail


def test_app_chat_send_runtime_error_merges_top_level_and_nested_details(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=123, username="alice", nickname="Alice", role="user")
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=123, visibility="private", status="running", name="Agent One")

    class _DB:
        def close(self):
            return None

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent))
    monkeypatch.setattr(web_module.runtime_execution_context_service, "build_runtime_metadata", lambda _db, _agent: {})

    async def _fake_forward(**_kwargs):
        payload = {
            "error": {
                "message": "request exceeded limit",
                "details": {
                    "incomplete_reason": "max_output_tokens",
                    "prompt_budget_tokens": 33000,
                },
            },
            "details": {
                "request_estimated_tokens": 34000,
                "prompt_budget_tokens": 32000,
                "reserved_output_tokens": 16000,
                "request_over_budget": True,
                "safety_margin_tokens": 1000,
                "max_prompt_tokens": 32000,
                "max_output_tokens": 16000,
                "payload": "HUGE",
                "token": "SECRET_TOKEN",
            },
        }
        return 500, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    client = TestClient(app)
    response = client.post("/app/chat/send", data={"agent_id": "agent-1", "message": "hi"})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "request exceeded limit" in detail
    assert "incomplete_reason=max_output_tokens" in detail
    assert "request_estimated_tokens=34000" in detail
    assert "prompt_budget_tokens=33000" in detail
    assert "reserved_output_tokens=16000" in detail
    assert "request_over_budget=True" in detail
    assert "safety_margin_tokens=1000" in detail
    assert "max_prompt_tokens=32000" in detail
    assert "max_output_tokens=16000" in detail
    assert "HUGE" not in detail
    assert "SECRET_TOKEN" not in detail


def test_app_chat_send_runtime_error_context_budget_exceeded_is_sanitized(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=123, username="alice", nickname="Alice", role="user")
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=123, visibility="private", status="running", name="Agent One")

    class _DB:
        def close(self):
            return None

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent))
    monkeypatch.setattr(web_module.runtime_execution_context_service, "build_runtime_metadata", lambda _db, _agent: {})

    async def _fake_forward(**_kwargs):
        payload = {
            "error": "LLM request remains over prompt budget after context projection.",
            "error_type": "context_budget_exceeded",
            "code": "context_budget_exceeded",
            "details": {
                "request_estimated_tokens": 50000,
                "prompt_budget_tokens": 32000,
                "reserved_output_tokens": 16000,
                "max_prompt_tokens": 32000,
                "max_output_tokens": 64000,
                "request_over_budget": True,
                "prompt": "SECRET",
                "payload": "SECRET_PAYLOAD",
                "input": "SECRET_INPUT",
                "output": "SECRET_OUTPUT",
                "api_key": "SECRET_KEY",
                "token": "SECRET_TOKEN",
            },
        }
        return 500, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    client = TestClient(app)
    response = client.post("/app/chat/send", data={"agent_id": "agent-1", "message": "hi"})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "Runtime error:" in detail
    assert "code=context_budget_exceeded" in detail
    assert "request_estimated_tokens=50000" in detail
    assert "prompt_budget_tokens=32000" in detail
    assert "reserved_output_tokens=16000" in detail
    assert "max_prompt_tokens=32000" in detail
    assert "max_output_tokens=64000" in detail
    assert "request_over_budget=True" in detail
    assert "SECRET" not in detail
    assert "prompt=" not in detail
    assert "payload=" not in detail
    assert "input=" not in detail
    assert "output=" not in detail
    assert "api_key=" not in detail
    assert "token=" not in detail
