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
    assert "action(`/api/agents/${agent.id}/restart`)" in render_agent_actions
    assert ":4096" not in render_agent_actions
    assert "/api/tasks" not in render_agent_actions
    assert "/api/internal" not in render_agent_actions

    assert 'agent.visibility === "public" ? "Unshare" : "Share"' in render_agent_actions
    assert 'agent.visibility === "public" ? "unshare" : "share"' in render_agent_actions


def test_render_agent_meta_defines_skill_repo_section_before_template_use_without_tools_repo_regression():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    render_agent_meta = _extract_js_function(js, "renderAgentMeta")

    repo_def_idx = render_agent_meta.index("let repoSection")
    render_idx = render_agent_meta.index("dom.agentMeta.innerHTML")
    repo_use_idx = render_agent_meta.index("${repoSection}")

    assert repo_def_idx < render_idx < repo_use_idx
    assert 'let repoSection = ""' in render_agent_meta or "let repoSection = ''" in render_agent_meta
    assert "if (effectiveSkillRepoUrl)" in render_agent_meta
    assert "Instructions Repository" in render_agent_meta
    assert "Skills Repository" in render_agent_meta
    assert "Agent Settings Repository" not in render_agent_meta
    assert "agent-skill-git-commit" in render_agent_meta
    assert "Using configured default" in render_agent_meta
    assert "Branch:" in render_agent_meta
    assert "${safe(effectiveSkillRepoUrl)}" in render_agent_meta
    assert "Branch: ${safe(effectiveSkillBranch)}" in render_agent_meta
    assert "id=\"agent-skill-git-commit\"" in render_agent_meta
    assert "fetchSkillGitInfo(agent.id)" in render_agent_meta

    assert "Tools Repository" not in render_agent_meta


def test_agent_health_card_is_wired_into_agent_meta():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    render_agent_meta = _extract_js_function(js, "renderAgentMeta")
    agent_health = _extract_js_function(js, "agentHealth")
    health_action = _extract_js_function(js, "handleAgentHealthAction")

    assert "agentHealthCardHtml(agent)" in render_agent_meta
    assert "data-agent-health-action" in render_agent_meta
    assert "Runtime profile is missing." in agent_health
    assert "Ready to chat." in agent_health
    assert "openEditDialog(agent)" in health_action
    assert 'action(`/api/agents/${agent.id}/restart`)' in health_action
    assert 'action(`/api/agents/${agent.id}/start`)' in health_action


def test_agent_list_keeps_search_and_compact_hover_status():
    template = Path("app/templates/app.html").read_text(encoding="utf-8")
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    render_agent_list = _extract_js_function(js, "renderAgentList")
    bind_events = _extract_js_function(js, "bindEvents")

    assert 'id="agent-search-input"' in template
    assert 'id="agent-scope-filter"' not in template
    assert 'id="agent-filter-clear"' not in template
    assert 'id="agent-status-filter"' not in template
    assert 'id="selected-status"' not in template
    assert "visibleAgents()" in render_agent_list
    assert "agentHealth(agent)" in render_agent_list
    assert "row.title =" in render_agent_list
    assert "portal-agent-status-dot" in render_agent_list
    assert "portal-agent-status-text" not in render_agent_list
    assert "portal-agent-health-line" not in render_agent_list
    assert "portal-agent-row-badges" in render_agent_list
    assert "dom.agentSearchInput?.addEventListener" in bind_events
    assert "[data-agent-status-filter]" not in bind_events


def test_agent_switch_updates_existing_rows_without_rerendering_list():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    render_agent_list = _extract_js_function(js, "renderAgentList")
    select_agent = _extract_js_function(js, "selectAgentById")
    sync_selection = _extract_js_function(js, "syncAgentListSelection")

    assert "row.dataset.agentId = agent.id" in render_agent_list
    assert "syncAgentListSelection(previousAgentId, agentId)" in select_agent
    assert "renderAgentList();" not in select_agent
    assert 'querySelectorAll(".portal-agent-row[data-agent-id]")' in sync_selection
    assert 'classList.toggle("is-active", active)' in sync_selection
    assert 'row.setAttribute("aria-current", "true")' in sync_selection
