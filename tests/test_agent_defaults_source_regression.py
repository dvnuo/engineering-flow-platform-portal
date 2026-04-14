from pathlib import Path


def test_template_create_form_has_no_hardcoded_agent_repo_or_branch_defaults():
    html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert 'value="https://github.com/dvnuo/engineering-flow-platform"' not in html
    assert 'name="branch" placeholder="master"' not in html
    assert 'name="branch" placeholder="Configured default branch" value="master"' not in html


def test_chat_ui_no_longer_hardcodes_master_or_main_branch_fallbacks():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert 'agent.branch || "master"' not in js
    assert "agent.branch || 'main'" not in js


def test_chat_ui_includes_agent_defaults_loading_and_application_helpers():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert "agentDefaults" in js
    assert "loadAgentDefaults" in js
    assert "applyCreateAgentDefaults" in js
