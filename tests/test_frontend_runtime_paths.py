from pathlib import Path



def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _chat_ui_js_source() -> str:
    chat_ui_path = _repo_root() / "app" / "static" / "js" / "chat_ui.js"
    return chat_ui_path.read_text(encoding="utf-8")

def test_chat_ui_uses_canonical_runtime_proxy_paths():
    js = _chat_ui_js_source()
    assert "/a/${agentId}/api/skill-git-info" in js
    assert "/a/${agentId}/api/usage" in js
    assert "/api/agents/${agentId}/git-info" not in js
    assert "/api/agents/${agentId}/usage" not in js
    assert "/a/${agentIdAtSend}/api/chat" in js
    assert 'hx-post="/app/chat/send"' not in js
    assert "htmx:beforeRequest" not in js
    assert "htmx:afterRequest" not in js
    assert "htmx:responseError" not in js


def test_settings_panel_no_longer_references_ssh_runtime_paths():
    template = Path("app/templates/partials/settings_panel.html").read_text(encoding="utf-8")
    assert "/a/${agentId}/api/ssh/public-key" not in template
    assert "/a/${agentId}/api/ssh/generate" not in template
    assert "/api/agents/${agentId}/ssh/public-key" not in template
    assert "/api/agents/${agentId}/ssh/generate" not in template
