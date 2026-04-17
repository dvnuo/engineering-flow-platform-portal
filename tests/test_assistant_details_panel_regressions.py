from pathlib import Path

from _js_extract_helpers import _extract_js_function


def test_render_agent_actions_has_no_settings_button_and_keeps_core_actions():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    render_agent_actions = _extract_js_function(js, "renderAgentActions")

    assert 'label: "Settings"' not in render_agent_actions
    assert 'onClick: () => openSettings()' not in render_agent_actions

    assert 'label: "Start"' in render_agent_actions
    assert 'label: "Stop"' in render_agent_actions
    assert 'label: "Restart"' in render_agent_actions
    assert 'label: "Edit"' in render_agent_actions
    assert 'label: "Delete"' in render_agent_actions
    assert 'label: "Destroy"' in render_agent_actions

    assert 'agent.visibility === "public" ? "Unshare" : "Share"' in render_agent_actions
    assert 'agent.visibility === "public" ? "unshare" : "share"' in render_agent_actions
