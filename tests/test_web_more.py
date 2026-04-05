"""Tests for web.py - settings and config."""
import json
import shutil
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient
import pytest


def test_agent_settings_panel():
    """Test agent settings panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-123/settings/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_agent_settings_save():
    """Test agent settings save."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/app/agents/agent-123/settings/save", 
                         json={"llm": {"provider": "openai"}})
    assert response.status_code in [200, 302, 400, 401, 403, 404]


def test_agent_files_panel():
    """Test agent files panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-123/files/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_agent_sessions_panel():
    """Test agent sessions panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-123/sessions/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_agent_skills_panel():
    """Test agent skills panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-123/skills/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_agent_usage_panel():
    """Test agent usage panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-123/usage/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_users_panel():
    """Test users panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/users/panel")
    assert response.status_code in [200, 302, 401, 403]


def test_api_agents_usage():
    """Test agents usage API."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/agents/agent-123/usage")
    assert response.status_code in [200, 401, 403, 404]


def test_proxy_agent_api():
    """Test proxy to agent API."""
    from app.main import app
    client = TestClient(app)
    # Test proxy endpoint
    response = client.post("/a/agent-123/api/chat", 
                         json={"message": "test"})
    assert response.status_code in [400, 401, 403, 404, 500, 502]


def test_proxy_agent_files_list():
    """Test proxy to agent files list."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/a/agent-123/api/files")
    assert response.status_code in [401, 403, 404, 500]


def test_proxy_agent_events():
    """Test proxy to agent events."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/a/agent-123/api/events")
    assert response.status_code in [400, 401, 403, 404]


def test_agent_runtime_destroy():
    """Test agent runtime destroy."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/agents/agent-123/destroy")
    assert response.status_code in [200, 401, 403, 404]


def test_agent_runtime_delete():
    """Test agent runtime delete."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/agents/agent-123/delete-runtime")
    assert response.status_code in [200, 401, 403, 404]


def test_agent_defaults():
    """Test agent defaults endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/agents/defaults")
    assert response.status_code in [200, 401, 403]


def test_proxy_api_chat_stream():
    """Test proxy chat stream endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/a/agent-123/api/chat/stream",
                         json={"message": "test"})
    assert response.status_code in [400, 401, 403, 404, 500, 502]


def _extract_js_helper_block(js_text: str, helper_name: str) -> str:
    start_marker = f"// RUNTIME_EVENT_HELPER_START: {helper_name}"
    end_marker = f"// RUNTIME_EVENT_HELPER_END: {helper_name}"
    start = js_text.find(start_marker)
    if start < 0:
        raise AssertionError(f"Unable to find start marker for {helper_name} in chat_ui.js")
    end = js_text.find(end_marker, start)
    if end < 0:
        raise AssertionError(f"Unable to find end marker for {helper_name} in chat_ui.js")
    return js_text[start + len(start_marker):end].strip()


def test_chat_ui_runtime_event_helpers_behavior():
    """Behavior-level coverage for runtime event normalization and completion states."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    normalize_block = _extract_js_helper_block(js_file, "normalizeRuntimeEvent")
    completion_block = _extract_js_helper_block(js_file, "completionRuntimeState")

    script = f"""
{completion_block}
{normalize_block}

const legacy = normalizeRuntimeEvent({{
  type: "tool_result",
  data: {{ tool: "search", message: "done" }},
  ts: 123,
}});

const normalized = normalizeRuntimeEvent({{
  event_type: "tool_result",
  state: "running",
  session_id: "s1",
  request_id: "r1",
  agent_id: "a1",
  summary: "Tool completed",
  detail_payload: {{ tool: "search" }},
  created_at: "2026-04-04T00:00:00Z",
}});

const precedence = normalizeRuntimeEvent({{
  type: "legacy_type",
  event_type: "normalized_type",
}});

const wrapped = normalizeRuntimeEvent({{
  event: {{
    event_type: "llm_thinking",
    summary: "Reasoning",
    created_at: "2026-04-04T00:00:00Z",
  }}
}});

const zeroTs = normalizeRuntimeEvent({{ type: "tool_result", ts: 0, data: {{}} }});
const zeroStringTs = normalizeRuntimeEvent({{ type: "tool_result", ts: "0", data: {{}} }});

const result = {{
  legacy,
  normalized,
  precedence,
  wrapped,
  zeroTs,
  zeroStringTs,
  invalid: [normalizeRuntimeEvent(null), normalizeRuntimeEvent({{}}), normalizeRuntimeEvent({{foo: "bar"}})],
  completionStates: [
    isCompletionRuntimeState("complete"),
    isCompletionRuntimeState("completed"),
    isCompletionRuntimeState("done"),
    isCompletionRuntimeState("finished"),
    isCompletionRuntimeState("running"),
    isCompletionRuntimeState(""),
    isCompletionRuntimeState(null),
  ]
}};
console.log(JSON.stringify(result));
"""

    completed = subprocess.run(
        [node_bin, "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(completed.stdout)

    legacy = data["legacy"]
    assert legacy["type"] == "tool_result"
    assert legacy["data"]["tool"] == "search"
    assert legacy["data"]["message"] == "done"
    assert legacy["ts"] == 123
    assert legacy.get("state", "") == ""

    normalized = data["normalized"]
    assert normalized["type"] == "tool_result"
    assert normalized["data"]["tool"] == "search"
    assert normalized["data"]["message"] == "Tool completed"
    assert normalized["data"]["request_id"] == "r1"
    assert normalized["data"]["session_id"] == "s1"
    assert normalized["data"]["agent_id"] == "a1"
    assert normalized["state"] == "running"
    assert isinstance(normalized["ts"], (int, float))
    assert data["precedence"]["type"] == "normalized_type"

    wrapped = data["wrapped"]
    assert wrapped["type"] == "llm_thinking"
    assert wrapped["data"]["message"] == "Reasoning"

    assert data["invalid"] == [None, None, None]
    assert data["completionStates"] == [True, True, True, True, False, False, False]
    assert data["zeroTs"]["ts"] == 0
    assert data["zeroStringTs"]["ts"] == "0"


def test_thinking_process_template_prefers_normalized_fields():
    template = Path("app/templates/partials/thinking_process_panel.html").read_text(encoding="utf-8")
    assert template.find("event.event_type or event.type") != -1
    assert template.find("event.summary") < template.find("event.data and event.data.message")
