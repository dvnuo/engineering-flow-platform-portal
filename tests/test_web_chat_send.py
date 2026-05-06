import json
from types import SimpleNamespace

from fastapi.testclient import TestClient


def test_app_chat_send_forwards_identity_and_trace_headers(monkeypatch):
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
    assert captured["extra_headers"]["X-Trace-Id"]
    assert captured["extra_headers"]["X-Span-Id"]
    assert "spoofed" not in captured["extra_headers"].values()
    assert captured["headers"] == {"content-type": "application/json"}


def test_app_chat_send_drops_form_identity_and_uses_portal_identity_and_trace_headers(monkeypatch):
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
    assert captured["extra_headers"]["X-Trace-Id"]
    assert captured["extra_headers"]["X-Span-Id"]
    assert "spoofed" not in captured["extra_headers"].values()


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
    assert "incomplete_reason=" not in detail


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
    assert "prompt_budget_tokens=" not in detail
    assert "request_estimated_tokens=" not in detail
    assert "reserved_output_tokens=" not in detail
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
    assert "incomplete_reason=" not in detail
    assert "request_estimated_tokens=" not in detail
    assert "prompt_budget_tokens=" not in detail
    assert "reserved_output_tokens=" not in detail
    assert "request_over_budget=" not in detail
    assert "max_prompt_tokens=32000" in detail
    assert "safety_margin_tokens=" not in detail
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
    assert "incomplete_reason=" not in detail
    assert "request_estimated_tokens=" not in detail
    assert "prompt_budget_tokens=" not in detail
    assert "reserved_output_tokens=" not in detail
    assert "request_over_budget=" not in detail
    assert "safety_margin_tokens=" not in detail
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
    assert "request_estimated_tokens=" not in detail
    assert "prompt_budget_tokens=" not in detail
    assert "reserved_output_tokens=" not in detail
    assert "max_prompt_tokens=32000" in detail
    assert "max_output_tokens=64000" in detail
    assert "request_over_budget=" not in detail
    assert "SECRET" not in detail
    assert "prompt=" not in detail
    assert "payload=" not in detail
    assert "input=" not in detail
    assert "output=" not in detail
    assert "api_key=" not in detail
    assert "token=" not in detail


def test_app_chat_send_runtime_error_includes_request_budget_stage(monkeypatch):
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
            "error": "over budget",
            "code": "context_budget_exceeded",
            "details": {
                "request_estimated_tokens": 50000,
                "prompt_budget_tokens": 32000,
                "request_budget_stage": "skill_finalizer",
                "prompt": "SECRET",
            },
        }
        return 500, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    client = TestClient(app)
    response = client.post("/app/chat/send", data={"agent_id": "agent-1", "message": "hi"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "request_budget_stage=" not in detail
    assert "prompt=" not in detail
    assert "SECRET" not in detail


def test_app_chat_send_runtime_error_uses_legacy_stage_as_fallback(monkeypatch):
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
            "error": "over budget",
            "code": "context_budget_exceeded",
            "details": {"stage": "tool_loop"},
        }
        return 500, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    client = TestClient(app)
    response = client.post("/app/chat/send", data={"agent_id": "agent-1", "message": "hi"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "request_budget_stage=" not in detail


def test_app_chat_send_runtime_error_prefers_request_budget_stage_over_stage(monkeypatch):
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
            "error": "over budget",
            "code": "context_budget_exceeded",
            "details": {"request_budget_stage": "skill_finalizer", "stage": "tool_loop"},
        }
        return 500, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    client = TestClient(app)
    response = client.post("/app/chat/send", data={"agent_id": "agent-1", "message": "hi"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "request_budget_stage=" not in detail


def test_app_chat_send_runtime_error_does_not_include_projection_diagnostics(monkeypatch):
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
            "error": "runtime failed",
            "code": "context_budget_exceeded",
            "details": {
                "projected_recent_assistant_messages": 5,
                "projected_plain_assistant_messages": 2,
                "assistant_projection_chars_saved": 1500,
                "output_size_guard_applied": True,
                "large_generation_guard_applied": True,
                "prompt": "SECRET_PROMPT",
                "payload": "SECRET_PAYLOAD",
                "input": "SECRET_INPUT",
                "output": "SECRET_OUTPUT",
                "raw_output": "SECRET_RAW_OUTPUT",
                "response": "SECRET_RESPONSE",
                "authorization": "Bearer SECRET_AUTH",
                "api_key": "SECRET_KEY",
                "token": "SECRET_TOKEN",
                "context_blob": {"jira_body": "SECRET_JIRA_BODY", "confluence_raw": "SECRET_CONFLUENCE_RAW"},
            },
        }
        return 500, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    client = TestClient(app)
    response = client.post("/app/chat/send", data={"agent_id": "agent-1", "message": "hi"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "projected_recent_assistant_messages=" not in detail
    assert "projected_plain_assistant_messages=" not in detail
    assert "assistant_projection_chars_saved=" not in detail
    assert "output_size_guard_applied=" not in detail
    assert "large_generation_guard_applied=" not in detail
    assert "prompt=" not in detail
    assert "payload=" not in detail
    assert "input=" not in detail
    assert "output=" not in detail
    assert "raw_output=" not in detail
    assert "response=" not in detail
    assert "authorization=" not in detail
    assert "api_key=" not in detail
    assert "token=" not in detail
    assert "SECRET_JIRA_BODY" not in detail
    assert "SECRET_CONFLUENCE_RAW" not in detail


def test_app_chat_send_runtime_error_does_not_include_legacy_source_generation_diagnostics(monkeypatch):
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
            "error": "runtime failed",
            "details": {
                "source_complete": False,
                "source_bundle_ref_count": 4,
                "source_digest_ref_count": 2,
                "comments_loaded": 11,
                "comments_total": 12,
                "attachments_loaded": 5,
                "attachments_total": 8,
                "source_partial_reasons_count": 3,
                "generation_mode": "staged",
                "current_generation_phase": "feature",
                "large_generation_guard_reason": "large_context_guard",
                "prompt": "SECRET_PROMPT",
                "payload": "SECRET_PAYLOAD",
                "input": "SECRET_INPUT",
                "output": "SECRET_OUTPUT",
                "authorization": "Bearer SECRET",
                "context_blob": {"jira": "SECRET_JIRA_BODY", "source_bundle": "SECRET_SOURCE_BUNDLE"},
                "source_bundle": {"raw": "SECRET_RAW_BUNDLE"},
            },
        }
        return 500, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    client = TestClient(app)
    response = client.post("/app/chat/send", data={"agent_id": "agent-1", "message": "hi"})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "source_complete=" not in detail
    assert "source_bundle_ref_count=" not in detail
    assert "source_digest_ref_count=" not in detail
    assert "comments_loaded=" not in detail
    assert "comments_total=" not in detail
    assert "attachments_loaded=" not in detail
    assert "attachments_total=" not in detail
    assert "source_partial_reasons_count=" not in detail
    assert "generation_mode=" not in detail
    assert "current_generation_phase=" not in detail
    assert "large_generation_guard_reason=" not in detail
    assert "prompt=" not in detail
    assert "payload=" not in detail
    assert "input=" not in detail
    assert "output=" not in detail
    assert "authorization=" not in detail
    assert "SECRET_JIRA_BODY" not in detail
    assert "SECRET_SOURCE_BUNDLE" not in detail
    assert "SECRET_RAW_BUNDLE" not in detail


def test_app_chat_send_runtime_error_does_not_include_non_allowlisted_output_recovery_scalars(monkeypatch):
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
            "error": "runtime failed",
            "details": {
                "source_type": "Confluence",
                "source_digest_chunk_count": 12,
                "children_loaded": 6,
                "children_total": 9,
                "output_risk_level": "high",
                "max_chat_output_chars": 8000,
                "max_output_recovery_applied": True,
                "max_output_recovery_attempts": 3,
                "output_token_limit": 2048,
                "input_context_usage_percent": 18.2,
                "prompt": "SECRET_PROMPT",
                "payload": "SECRET_PAYLOAD",
                "input": "SECRET_INPUT",
                "output": "SECRET_OUTPUT",
                "raw_output": "SECRET_RAW",
                "response": "SECRET_RESPONSE",
                "authorization": "Bearer SECRET",
                "api_key": "SECRET_KEY",
                "token": "SECRET_TOKEN",
                "jira_raw": "SECRET_JIRA",
                "confluence_raw": "SECRET_CONF",
                "context_blob": {"raw": "SECRET_CONTEXT"},
                "ctx_refs": ["ctx://ref/1"],
            },
        }
        return 500, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    client = TestClient(app)
    response = client.post("/app/chat/send", data={"agent_id": "agent-1", "message": "hi"})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "source_type=" not in detail
    assert "source_digest_chunk_count=" not in detail
    assert "children_loaded=" not in detail
    assert "children_total=" not in detail
    assert "output_risk_level=" not in detail
    assert "max_chat_output_chars=8000" in detail
    assert "max_output_recovery_applied=" not in detail
    assert "max_output_recovery_attempts=" not in detail
    assert "output_token_limit=" not in detail
    assert "input_context_usage_percent=" not in detail
    assert "prompt=" not in detail
    assert "payload=" not in detail
    assert "input=" not in detail
    assert "output=" not in detail
    assert "raw_output=" not in detail
    assert "response=" not in detail
    assert "authorization=" not in detail
    assert "api_key=" not in detail
    assert "token=" not in detail
    assert "SECRET_JIRA" not in detail
    assert "SECRET_CONF" not in detail
    assert "SECRET_CONTEXT" not in detail
    assert "ctx://" not in detail


def test_app_chat_send_runtime_error_typeerror_includes_safe_controller_fields(monkeypatch):
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
            "error": "int() argument must be a string, a bytes-like object or a real number, not 'NoneType'",
            "error_type": "TypeError",
            "details": {
                "max_context_window_tokens": 400000,
                "max_prompt_tokens": 272000,
                "max_output_tokens": 128000,
                "effective_max_tokens": 128000,
                "legacy_max_tokens_ignored": True,
                "configured_max_tokens": 64000,
                "max_chat_output_tokens": 120000,
                "max_chat_output_chars": 480000,
                "output_boundary_source": "model_limits",
                "legacy_max_chat_output_chars_ignored": True,
                "configured_max_chat_output_chars": 8000,
                "budget_max_chat_output_chars_ignored": True,
                "configured_budget_max_chat_output_chars": 8000,
                "arg_max_chat_output_chars_ignored": True,
                "configured_arg_max_chat_output_chars": 8000,
                "file_context_budget_status": "within_limit",
                "file_context_estimated_tokens": 1500,
                "file_context_threshold_source": "resolved_runtime_profile",
                "chars_per_token_estimate": 4.0,
                "output_risk_level": "medium",
                "input_context_usage_percent": 22.5,
                "prompt": "SECRET_PROMPT",
                "payload": "SECRET_PAYLOAD",
                "context_blob": {"raw": "SECRET_CONTEXT"},
            },
        }
        return 500, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    client = TestClient(app)
    response = client.post("/app/chat/send", data={"agent_id": "agent-1", "message": "hi"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "Runtime error: int() argument must be a string" in detail
    assert "error_type=TypeError" in detail
    assert "max_context_window_tokens=400000" in detail
    assert "max_prompt_tokens=272000" in detail
    assert "max_output_tokens=128000" in detail
    assert "effective_max_tokens=128000" in detail
    assert "legacy_max_tokens_ignored=True" in detail
    assert "configured_max_tokens=64000" in detail
    assert "max_chat_output_tokens=120000" in detail
    assert "max_chat_output_chars=480000" in detail
    assert "output_boundary_source=model_limits" in detail
    assert "legacy_max_chat_output_chars_ignored=True" in detail
    assert "configured_max_chat_output_chars=8000" in detail
    assert "budget_max_chat_output_chars_ignored=True" in detail
    assert "configured_budget_max_chat_output_chars=8000" in detail
    assert "arg_max_chat_output_chars_ignored=True" in detail
    assert "configured_arg_max_chat_output_chars=8000" in detail
    assert "file_context_budget_status=within_limit" in detail
    assert "file_context_estimated_tokens=1500" in detail
    assert "file_context_threshold_source=resolved_runtime_profile" in detail
    assert "chars_per_token_estimate=4.0" in detail
    assert "output_risk_level=" not in detail
    assert "input_context_usage_percent=" not in detail
    assert "prompt=" not in detail
    assert "payload=" not in detail
    assert "SECRET_CONTEXT" not in detail


def test_app_chat_send_runtime_error_includes_model_limit_scalars_only(monkeypatch):
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
            "error": "runtime failed",
            "details": {
                "max_context_window_tokens": 400000,
                "max_prompt_tokens": 272000,
                "max_output_tokens": 128000,
                "effective_max_tokens": 128000,
                "legacy_max_tokens_ignored": True,
                "configured_max_tokens": 64000,
                "max_chat_output_tokens": 120000,
                "max_chat_output_chars": 480000,
                "output_boundary_source": "model_limits_derived",
                "legacy_max_chat_output_chars_ignored": True,
                "configured_max_chat_output_chars": 8000,
                "budget_max_chat_output_chars_ignored": True,
                "configured_budget_max_chat_output_chars": 8000,
                "arg_max_chat_output_chars_ignored": True,
                "configured_arg_max_chat_output_chars": 8000,
                "file_context_budget_status": "within_limit",
                "file_context_estimated_tokens": 1500,
                "file_context_threshold_source": "resolved_runtime_profile",
                "chars_per_token_estimate": 4.0,
                "output_risk_level": "high",
                "input_context_usage_percent": 35.0,
                "prompt": "SECRET_PROMPT",
                "payload": "SECRET_PAYLOAD",
                "input": "SECRET_INPUT",
                "output": "SECRET_OUTPUT",
                "response": "SECRET_RESPONSE",
                "authorization": "Bearer SECRET",
                "api_key": "SECRET_KEY",
                "token": "SECRET_TOKEN",
                "context_blob": {"jira": "SECRET_JIRA"},
                "ctx_refs": ["ctx://raw/1"],
            },
        }
        return 500, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    client = TestClient(app)
    response = client.post("/app/chat/send", data={"agent_id": "agent-1", "message": "hi"})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "max_context_window_tokens=400000" in detail
    assert "max_prompt_tokens=272000" in detail
    assert "max_output_tokens=128000" in detail
    assert "effective_max_tokens=128000" in detail
    assert "legacy_max_tokens_ignored=True" in detail
    assert "configured_max_tokens=64000" in detail
    assert "max_chat_output_tokens=120000" in detail
    assert "max_chat_output_chars=480000" in detail
    assert "output_boundary_source=model_limits_derived" in detail
    assert "legacy_max_chat_output_chars_ignored=True" in detail
    assert "configured_max_chat_output_chars=8000" in detail
    assert "budget_max_chat_output_chars_ignored=True" in detail
    assert "configured_budget_max_chat_output_chars=8000" in detail
    assert "arg_max_chat_output_chars_ignored=True" in detail
    assert "configured_arg_max_chat_output_chars=8000" in detail
    assert "file_context_budget_status=within_limit" in detail
    assert "file_context_estimated_tokens=1500" in detail
    assert "file_context_threshold_source=resolved_runtime_profile" in detail
    assert "chars_per_token_estimate=4.0" in detail
    assert "output_risk_level=" not in detail
    assert "input_context_usage_percent=" not in detail
    assert "jira_comments_bundle_ref_count=" not in detail
    assert "prompt=" not in detail
    assert "payload=" not in detail
    assert "input=" not in detail
    assert "output=" not in detail
    assert "response=" not in detail
    assert "authorization=" not in detail
    assert "api_key=" not in detail
    assert "token=" not in detail
    assert "SECRET_JIRA" not in detail
    assert "ctx://" not in detail


def test_app_chat_send_runtime_error_includes_model_limit_phase_scalars_only(monkeypatch):
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
            "error": "runtime failed",
            "details": {
                "max_context_window_tokens": 400000,
                "max_prompt_tokens": 272000,
                "max_output_tokens": 128000,
                "effective_max_tokens": 128000,
                "legacy_max_tokens_ignored": True,
                "configured_max_tokens": 64000,
                "max_chat_output_tokens": 120000,
                "max_chat_output_chars": 480000,
                "output_boundary_source": "model_limits",
                "legacy_max_chat_output_chars_ignored": True,
                "configured_max_chat_output_chars": 8000,
                "budget_max_chat_output_chars_ignored": True,
                "configured_budget_max_chat_output_chars": 8000,
                "arg_max_chat_output_chars_ignored": True,
                "configured_arg_max_chat_output_chars": 8000,
                "file_context_budget_status": "within_limit",
                "file_context_estimated_tokens": 1500,
                "file_context_threshold_source": "resolved_runtime_profile",
                "chars_per_token_estimate": 4.0,
                "output_risk_level": "normal",
                "input_context_usage_percent": 18.5,
                "prompt": "SECRET_PROMPT",
                "payload": "SECRET_PAYLOAD",
                "input": "SECRET_INPUT",
                "output": "SECRET_OUTPUT",
                "raw_output": "SECRET_RAW_OUTPUT",
                "response": "SECRET_RESPONSE",
                "authorization": "Bearer SECRET",
                "api_key": "SECRET_KEY",
                "token": "SECRET_TOKEN",
                "jira_raw": "SECRET_JIRA",
                "context_blob": {"raw": "SECRET_CONTEXT"},
                "ctx_refs": ["ctx://raw/1"],
            },
        }
        return 500, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    client = TestClient(app)
    response = client.post("/app/chat/send", data={"agent_id": "agent-1", "message": "hi"})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "max_context_window_tokens=400000" in detail
    assert "max_prompt_tokens=272000" in detail
    assert "max_output_tokens=128000" in detail
    assert "effective_max_tokens=128000" in detail
    assert "legacy_max_tokens_ignored=True" in detail
    assert "configured_max_tokens=64000" in detail
    assert "max_chat_output_tokens=120000" in detail
    assert "max_chat_output_chars=480000" in detail
    assert "output_boundary_source=model_limits" in detail
    assert "legacy_max_chat_output_chars_ignored=True" in detail
    assert "configured_max_chat_output_chars=8000" in detail
    assert "budget_max_chat_output_chars_ignored=True" in detail
    assert "configured_budget_max_chat_output_chars=8000" in detail
    assert "arg_max_chat_output_chars_ignored=True" in detail
    assert "configured_arg_max_chat_output_chars=8000" in detail
    assert "file_context_budget_status=within_limit" in detail
    assert "file_context_estimated_tokens=1500" in detail
    assert "file_context_threshold_source=resolved_runtime_profile" in detail
    assert "chars_per_token_estimate=4.0" in detail
    assert "output_risk_level=" not in detail
    assert "input_context_usage_percent=" not in detail
    assert "prompt=" not in detail
    assert "payload=" not in detail
    assert "input=" not in detail
    assert "output=" not in detail
    assert "raw_output=" not in detail
    assert "response=" not in detail
    assert "authorization=" not in detail
    assert "api_key=" not in detail
    assert "token=" not in detail
    assert "SECRET_JIRA" not in detail
    assert "SECRET_CONTEXT" not in detail
    assert "ctx://" not in detail


def test_app_chat_send_runtime_error_includes_only_safe_model_limit_scalars(monkeypatch):
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
            "error": "runtime failed",
            "details": {
                "max_context_window_tokens": 400000,
                "max_prompt_tokens": 272000,
                "max_output_tokens": 128000,
                "effective_max_tokens": 128000,
                "legacy_max_tokens_ignored": True,
                "configured_max_tokens": 64000,
                "max_chat_output_tokens": 120000,
                "max_chat_output_chars": 480000,
                "output_boundary_source": "model_limits",
                "legacy_max_chat_output_chars_ignored": True,
                "configured_max_chat_output_chars": 8000,
                "budget_max_chat_output_chars_ignored": True,
                "configured_budget_max_chat_output_chars": 8000,
                "arg_max_chat_output_chars_ignored": True,
                "configured_arg_max_chat_output_chars": 8000,
                "file_context_budget_status": "within_limit",
                "file_context_estimated_tokens": 1500,
                "file_context_threshold_source": "resolved_runtime_profile",
                "chars_per_token_estimate": 4.0,
                "output_risk_level": "medium",
                "input_context_usage_percent": 42.0,
                "prompt": "SECRET_PROMPT",
                "payload": "SECRET_PAYLOAD",
                "input": "SECRET_INPUT",
                "output": "SECRET_OUTPUT",
                "raw_output": "SECRET_RAW",
                "response": "SECRET_RESPONSE",
                "authorization": "Bearer SECRET",
                "api_key": "SECRET_KEY",
                "token": "SECRET_TOKEN",
                "context_blob": {"raw": "SECRET_CONTEXT"},
                "ctx_refs": ["ctx://leak/1"],
            },
        }
        return 500, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    client = TestClient(app)
    response = client.post("/app/chat/send", data={"agent_id": "agent-1", "message": "hi"})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "max_context_window_tokens=400000" in detail
    assert "max_prompt_tokens=272000" in detail
    assert "max_output_tokens=128000" in detail
    assert "effective_max_tokens=128000" in detail
    assert "legacy_max_tokens_ignored=True" in detail
    assert "configured_max_tokens=64000" in detail
    assert "max_chat_output_tokens=120000" in detail
    assert "max_chat_output_chars=480000" in detail
    assert "output_boundary_source=model_limits" in detail
    assert "legacy_max_chat_output_chars_ignored=True" in detail
    assert "configured_max_chat_output_chars=8000" in detail
    assert "budget_max_chat_output_chars_ignored=True" in detail
    assert "configured_budget_max_chat_output_chars=8000" in detail
    assert "arg_max_chat_output_chars_ignored=True" in detail
    assert "configured_arg_max_chat_output_chars=8000" in detail
    assert "file_context_budget_status=within_limit" in detail
    assert "file_context_estimated_tokens=1500" in detail
    assert "file_context_threshold_source=resolved_runtime_profile" in detail
    assert "chars_per_token_estimate=4.0" in detail
    assert "output_risk_level=" not in detail
    assert "input_context_usage_percent=" not in detail
    assert "jira_comments_bundle_ref_count=" not in detail
    assert "prompt=" not in detail
    assert "payload=" not in detail
    assert "input=" not in detail
    assert "output=" not in detail
    assert "raw_output=" not in detail
    assert "response=" not in detail
    assert "authorization=" not in detail
    assert "api_key=" not in detail
    assert "token=" not in detail
    assert "SECRET_CONTEXT" not in detail
    assert "ctx://" not in detail
    assert "raw_output=" not in detail


def test_app_chat_send_does_not_forward_browser_spoofed_trace_header(monkeypatch):
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
        lambda _db, _agent: {},
    )

    captured = {}

    async def _fake_forward(**kwargs):
        captured.update(kwargs)
        return 200, json.dumps({"response": "ok", "session_id": "s-1", "events": []}).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)

    client = TestClient(app)
    response = client.post(
        "/app/chat/send",
        data={"agent_id": "agent-1", "message": "hi"},
        headers={"X-Trace-Id": "browser-spoof", "X-Request-Id": "browser-request-spoof"},
    )

    assert response.status_code == 200
    assert captured["extra_headers"]["X-Trace-Id"]
    assert captured["extra_headers"]["X-Trace-Id"] != "browser-spoof"
    assert captured["extra_headers"]["X-Trace-Id"] != "browser-request-spoof"
    assert response.headers["X-Trace-Id"] != "browser-spoof"
    assert response.headers["X-Trace-Id"] != "browser-request-spoof"
