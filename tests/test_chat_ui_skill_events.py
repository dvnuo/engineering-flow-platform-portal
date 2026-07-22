from pathlib import Path


def test_chat_ui_includes_active_skill_contract_events():
    source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")

    # Skill/context runtime events remain tracked by the event stream (isTrackableStreamEvent).
    # The per-event display titles previously lived only in the removed Thinking Process panel
    # (getThinkingEventDisplay) and are intentionally gone with it.
    assert '"skill_runtime_applied"' in source
    assert '"skill_contract_active"' in source
    assert '"skill_tool_denied"' in source
    assert '"skill_contract_cleared"' in source
    assert '"context_snapshot"' in source
    assert '"context_compaction_applied"' in source
