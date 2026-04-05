from pathlib import Path


def test_chat_ui_uses_canonical_runtime_proxy_paths():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert "`/a/${agentId}/api/git-info`" in js
    assert "`/a/${agentId}/api/usage`" in js
    assert "`/api/agents/${agentId}/git-info`" not in js
    assert "`/api/agents/${agentId}/usage`" not in js


def test_settings_panel_uses_canonical_runtime_proxy_paths_for_ssh():
    template = Path("app/templates/partials/settings_panel.html").read_text(encoding="utf-8")
    assert "`/a/${agentId}/api/ssh/public-key`" in template
    assert "`/a/${agentId}/api/ssh/generate`" in template
    assert "`/api/agents/${agentId}/ssh/public-key`" not in template
    assert "`/api/agents/${agentId}/ssh/generate`" not in template
