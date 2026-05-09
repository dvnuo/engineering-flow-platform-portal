from pathlib import Path


def _chat_ui_js_source() -> str:
    return Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")


def test_chat_ui_skill_slash_helpers_and_metadata_markers_present():
    js = _chat_ui_js_source()
    assert "function parseSkillSlashInput(text)" in js
    assert r"/^\/([A-Za-z0-9][A-Za-z0-9_-]*)(?:\s+(.*))?$/" in js
    assert 'replaceAll("_", "-")' in js
    assert "function findCachedSkillForSlash(invocation, agentId = state.selectedAgentId)" in js
    assert "slash_command:" in js
    assert 'source: "portal-chat-ui"' in js
    assert 'runtime_equivalence: skill?.runtime_equivalence ?? ""' in js
    assert 'data.reason || data.blocked_reason || data.message || "Skill blocked"' in js


def test_chat_ui_skill_non_callable_blocker_present_and_returns_before_send():
    js = _chat_ui_js_source()
    assert "matchedSkill.callable === false" in js
    assert "blocked_reason" in js
    blocker_idx = js.find("matchedSkill.callable === false")
    send_idx = js.find("fetch(`/a/${agentIdAtSend}/api/chat`")
    assert blocker_idx != -1 and send_idx != -1 and blocker_idx < send_idx
    blocker_slice = js[blocker_idx:send_idx]
    assert "return;" in blocker_slice


def test_chat_ui_runtime_proxy_paths_still_used_for_chat_send():
    js = _chat_ui_js_source()
    assert "/a/${agentIdAtSend}/api/chat" in js
    assert "/a/${agentIdAtSend}/api/chat/stream" in js
    assert "/api/agents/${agentIdAtSend}/" not in js
