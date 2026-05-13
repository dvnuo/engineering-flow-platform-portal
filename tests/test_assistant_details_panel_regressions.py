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


def test_render_agent_meta_defines_skill_repo_section_before_template_use_without_tools_repo_regression():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    render_agent_meta = _extract_js_function(js, "renderAgentMeta")

    runtime_type_idx = render_agent_meta.index('const runtimeType = String(agent.runtime_type || "native").trim().toLowerCase() || "native";')
    repo_def_idx = render_agent_meta.index("let repoSection")
    template_idx = render_agent_meta.index("dom.agentMeta.innerHTML")
    repo_use_idx = render_agent_meta.index("${repoSection}")

    assert runtime_type_idx < repo_def_idx < template_idx < repo_use_idx
    assert 'let repoSection = ""' in render_agent_meta or "let repoSection = ''" in render_agent_meta
    assert "if (effectiveSkillRepoUrl)" in render_agent_meta
    assert "Skills Repository" in render_agent_meta
    assert "agent-skill-git-commit" in render_agent_meta
    assert "Using configured default" in render_agent_meta
    assert "Branch:" in render_agent_meta
    assert "${safe(effectiveSkillRepoUrl)}" in render_agent_meta
    assert "Branch: ${safe(effectiveSkillBranch)}" in render_agent_meta
    assert "id=\"agent-skill-git-commit\"" in render_agent_meta
    assert "fetchSkillGitInfo(agent.id)" in render_agent_meta

    # #318 intentionally removed Portal tools repo/branch UI/config/provisioning.
    # This regression fix must not reintroduce those fields.
    assert "tool_repo" not in render_agent_meta
    assert "tool_branch" not in render_agent_meta
    assert "toolRepoSection" not in render_agent_meta
    assert "Tools Repository" not in render_agent_meta
    assert "default_tool_repo_url" not in render_agent_meta
    assert "default_tool_branch" not in render_agent_meta
    assert "agent.tool_repo_url" not in render_agent_meta
    assert "agent.tool_branch" not in render_agent_meta
