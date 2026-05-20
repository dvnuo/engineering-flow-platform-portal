from pathlib import Path

from tests._js_extract_helpers import _extract_js_function


SRC = Path("app/static/js/chat_ui.js")


def test_agent_restart_ui_waits_for_status_endpoint_before_completed_message():
    src = SRC.read_text(encoding="utf-8")
    action_fn = _extract_js_function(src, "action")
    poll_fn = _extract_js_function(src, "pollAgentUntilRestartComplete")

    assert "function applyLocalAgentStatus" in src
    assert "async function pollAgentUntilRestartComplete" in src
    assert 'setChatStatus("Assistant restarted.")' not in action_fn
    assert "Restart requested" in action_fn
    assert "Restarting assistant" in action_fn
    assert "`/api/agents/${encodeURIComponent(agentId)}/status`" in poll_fn
    assert 'if (status === "running")' in poll_fn
    assert 'setChatStatus("Assistant restart completed.")' in poll_fn
    assert "loadSessionForAgent(lifecycle.agentId" not in action_fn
    assert "loadSessionForAgent(agentId, chatState.sessionId" in poll_fn
