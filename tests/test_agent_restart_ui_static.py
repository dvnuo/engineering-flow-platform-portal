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


def test_restart_action_starts_poll_after_refresh_without_post_refresh_stale_apply():
    src = SRC.read_text(encoding="utf-8")
    action_fn = _extract_js_function(src, "action")
    restart_start = action_fn.index('if (lifecycle.action === "restart")')
    restart_end = action_fn.index("return;", restart_start)
    restart_branch = action_fn[restart_start:restart_end]

    refresh_idx = restart_branch.index("await refreshAll({ preserveLayout: true });")
    poll_idx = restart_branch.index("pollAgentUntilRestartComplete(lifecycle.agentId)")

    assert refresh_idx < poll_idx
    assert restart_branch.count("applyLocalAgentStatus(") == 1
    assert "cachedStatus" in restart_branch
    assert '["running", "failed", "stopped", "deleting"].includes(cachedStatus)' in restart_branch


def test_restart_action_catch_handles_restart_errors_without_rethrow():
    src = SRC.read_text(encoding="utf-8")
    action_fn = _extract_js_function(src, "action")
    catch_start = action_fn.index("} catch (error) {")
    restart_catch_start = action_fn.index("if (isRestartAction)", catch_start)
    restart_catch_end = action_fn.index("throw error;", restart_catch_start)
    restart_catch = action_fn[restart_catch_start:restart_catch_end]

    assert "await refreshAll({ preserveLayout: true });" in restart_catch
    assert "return;" in restart_catch
