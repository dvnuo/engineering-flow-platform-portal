from pathlib import Path


def test_chat_ui_includes_active_skill_contract_events():
    source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")

    assert '"skill_runtime_applied"' in source
    assert '"skill_contract_active"' in source
    assert '"skill_tool_denied"' in source
    assert '"skill_contract_cleared"' in source
    assert '"context_snapshot"' in source
    assert '"context_compaction_applied"' in source
    assert "Skill Runtime Applied" in source
    assert "Active Skill Cleared" in source
    assert "Context Snapshot" in source
    assert "Context Compaction Applied" in source
