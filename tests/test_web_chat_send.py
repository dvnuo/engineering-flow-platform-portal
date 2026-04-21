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
    assert "request_budget_stage=skill_finalizer" in detail
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
    assert "request_budget_stage=tool_loop" in detail


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
    assert "request_budget_stage=skill_finalizer" in detail
    assert "request_budget_stage=tool_loop" not in detail


def test_app_chat_send_runtime_error_includes_new_safe_projection_diagnostics_only(monkeypatch):
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
    assert "projected_recent_assistant_messages=5" in detail
    assert "projected_plain_assistant_messages=2" in detail
    assert "assistant_projection_chars_saved=1500" in detail
    assert "output_size_guard_applied=True" in detail
    assert "large_generation_guard_applied=True" in detail
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


def test_app_chat_send_runtime_error_includes_safe_source_and_generation_diagnostics_only(monkeypatch):
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
    assert "source_complete=False" in detail
    assert "source_bundle_ref_count=4" in detail
    assert "source_digest_ref_count=2" in detail
    assert "comments_loaded=11" in detail
    assert "comments_total=12" in detail
    assert "attachments_loaded=5" in detail
    assert "attachments_total=8" in detail
    assert "source_partial_reasons_count=3" in detail
    assert "generation_mode=staged" in detail
    assert "current_generation_phase=feature" in detail
    assert "large_generation_guard_reason=large_context_guard" in detail
    assert "prompt=" not in detail
    assert "payload=" not in detail
    assert "input=" not in detail
    assert "output=" not in detail
    assert "authorization=" not in detail
    assert "SECRET_JIRA_BODY" not in detail
    assert "SECRET_SOURCE_BUNDLE" not in detail
    assert "SECRET_RAW_BUNDLE" not in detail


def test_app_chat_send_runtime_error_includes_new_safe_output_recovery_diagnostics_only(monkeypatch):
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
    assert "source_type=Confluence" in detail
    assert "source_digest_chunk_count=12" in detail
    assert "children_loaded=6" in detail
    assert "children_total=9" in detail
    assert "output_risk_level=high" in detail
    assert "max_chat_output_chars=8000" in detail
    assert "max_output_recovery_applied=True" in detail
    assert "max_output_recovery_attempts=3" in detail
    assert "output_token_limit=2048" in detail
    assert "input_context_usage_percent=18.2" in detail
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


def test_app_chat_send_runtime_error_includes_new_attachment_and_oversized_output_scalars_only(monkeypatch):
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
                "comments_complete": True,
                "attachments_complete": False,
                "children_complete": True,
                "text_attachments_loaded": 4,
                "text_attachments_total": 6,
                "text_attachments_complete": False,
                "text_attachments_preview_only": 2,
                "binary_attachment_bodies_skipped_count": 3,
                "attachment_body_complete": True,
                "max_chat_output_enforced": True,
                "oversized_output_saved": True,
                "oversized_output_ref_count": 2,
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
    assert "comments_complete=True" in detail
    assert "attachments_complete=False" in detail
    assert "children_complete=True" in detail
    assert "text_attachments_loaded=4" in detail
    assert "text_attachments_total=6" in detail
    assert "text_attachments_complete=False" in detail
    assert "text_attachments_preview_only=2" in detail
    assert "binary_attachment_bodies_skipped_count=3" in detail
    assert "attachment_body_complete=True" in detail
    assert "max_chat_output_enforced=True" in detail
    assert "oversized_output_saved=True" in detail
    assert "oversized_output_ref_count=2" in detail
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


def test_app_chat_send_runtime_error_includes_output_controller_phase_scalars_only(monkeypatch):
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
                "generation_completed_phases_count": 3,
                "generation_next_phase": "step_definitions",
                "generation_state_active": True,
                "output_controller_applied": True,
                "source_context_mode": "preview",
                "default_source_complete_applied": True,
                "source_preview_tool_used": False,
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
    assert "generation_completed_phases_count=3" in detail
    assert "generation_next_phase=step_definitions" in detail
    assert "generation_state_active=True" in detail
    assert "output_controller_applied=True" in detail
    assert "source_context_mode=preview" in detail
    assert "default_source_complete_applied=True" in detail
    assert "source_preview_tool_used=False" in detail
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


def test_app_chat_send_runtime_error_includes_source_ref_and_controller_recovery_scalars_only(monkeypatch):
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
                "source_ref_session_valid": True,
                "default_source_complete_ref_session": "current",
                "model_facing_preview_tool_available": True,
                "preview_tool_used": False,
                "output_controller_stage": "initial_plan",
                "output_controller_recovery_reason": "oversized_output",
                "oversized_output_saved": True,
                "oversized_output_ref_count": 2,
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
    assert "source_ref_session_valid=True" in detail
    assert "default_source_complete_ref_session=current" in detail
    assert "model_facing_preview_tool_available=True" in detail
    assert "preview_tool_used=False" in detail
    assert "output_controller_stage=initial_plan" in detail
    assert "output_controller_recovery_reason=oversized_output" in detail
    assert "oversized_output_saved=True" in detail
    assert "oversized_output_ref_count=2" in detail
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
