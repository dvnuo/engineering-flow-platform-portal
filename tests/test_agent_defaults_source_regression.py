from pathlib import Path



def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _chat_ui_js_source() -> str:
    chat_ui_path = _repo_root() / "app" / "static" / "js" / "chat_ui.js"
    return chat_ui_path.read_text(encoding="utf-8")

def test_template_create_form_has_no_hardcoded_agent_repo_or_branch_defaults():
    html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert 'value="https://github.com/dvnuo/engineering-flow-platform"' not in html
    assert 'name="branch" placeholder="master"' not in html
    assert 'name="branch" placeholder="Configured default branch" value="master"' not in html


def test_chat_ui_no_longer_hardcodes_master_or_main_branch_fallbacks():
    js = _chat_ui_js_source()
    assert 'agent.branch || "master"' not in js
    assert "agent.branch || 'main'" not in js


def test_chat_ui_includes_agent_defaults_loading_and_application_helpers():
    js = _chat_ui_js_source()
    assert "agentDefaults" in js
    assert "loadAgentDefaults" in js
    assert "applyCreateAgentDefaults" in js
