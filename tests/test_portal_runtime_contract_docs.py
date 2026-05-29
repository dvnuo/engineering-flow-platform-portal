from pathlib import Path


def test_portal_runtime_contract_doc_and_readme_alignment():
    doc_path = Path("docs/PORTAL_RUNTIME_CONTRACT.md")
    assert doc_path.exists()
    text = doc_path.read_text(encoding="utf-8")
    assert "/a/{agent_id}/api/*" in text
    assert "/app/skills" in text
    assert "/app/tools" not in text
    assert "ENABLE_RUNTIME_SOURCE_OVERLAY" in text
    assert "X-Trace-Id" in text
    assert "runtime_type" in text
    assert "/root/.local/share/opencode" in text
    assert "/root/.local/share/efp-compat" in text
    assert "/home/opencode" not in text
    assert "EFP_OPENCODE_PERMISSION_MODE" in text
    assert "EFP_OPENCODE_ALLOW_BASH_ALL" in text
    assert "OpenCode on-demand repository checkout contract" in text
    assert "/workspace/repos/<owner>/<repo>" in text
    assert "`GIT_TOKEN` remains initContainer-only" in text

    assert "runtime/tools index" not in text
    assert "tools repo/runtime tools index" not in text
    assert "tools-index" not in text
    assert "tools_index" not in text
    assert "EFP_TOOLS_DIR" not in text
    assert "OPENCODE_TOOLS_DIR" not in text
    assert "Portal provisions skills/tools" not in text
    assert "runtime capability snapshot" in text
    assert "built-in" in text
    assert "runtime profile" in text
    assert "permission policy" in text
    for tool_id in [
        "apply_patch",
        "bash",
        "edit",
        "glob",
        "grep",
        "invalid",
        "read",
        "skill",
        "task",
        "todowrite",
        "webfetch",
        "write",
    ]:
        assert tool_id in text
    assert "enabled_tools" in text
    assert "disabled_tools" in text
    assert "tool_permissions" in text
    assert "max_iterations" in text
    assert "compaction_preserve_recent_tokens" in text
    assert "include_default_system_prompt" in text
    assert "Dedicated UI controls for every Runtime v2 field are not part of this pass" in text
    assert "compaction_preserve_recent_" "turns" not in text
    assert "RuntimeConfig" in text
    assert "runtime_profile.config" in text
    assert 'llm.tools=["*"]' in text
    assert "Removed legacy aliases" in text
    assert "runtime-owned compatibility decisions" in text
    assert "summary, revert, and unrevert" in text
    assert "GET /a/{agent_id}/api/sessions" in text
    assert "DELETE /a/{agent_id}/api/sessions/{session_id}" in text
    assert "GET /a/{agent_id}/api/sessions/{session_id}/chatlog" in text


    readme = Path("README.md").read_text(encoding="utf-8")
    assert "docs/PORTAL_RUNTIME_CONTRACT.md" in readme
    assert "runtime/tools index" not in readme
    assert "tools repo/runtime tools index" not in readme
    assert "/app/tools" not in readme
    assert "EFP_TOOLS_DIR" not in readme
    assert "OPENCODE_TOOLS_DIR" not in readme
    assert "native runtime clones runtime repo + skill repo via initContainers" not in readme
    assert "DEFAULT_OPENCODE_PERMISSION_MODE" in readme
    assert "DEFAULT_OPENCODE_ALLOW_BASH_ALL" in readme
    assert "workspace_full_access" in readme
    assert "EFP_WORKSPACE_REPOS_DIR=/workspace/repos" in readme
    assert "EFP_GIT_CHECKOUT_TIMEOUT_SECONDS=120" in readme

    phase5 = Path("docs/PHASE5_PRODUCTIZATION.md").read_text(encoding="utf-8")
    assert "Runtime v2 capability snapshots" in phase5
    assert "apply_patch" in phase5
    assert "todowrite" in phase5
    assert "Runtime profile apply/config contract" in phase5
    assert "enabled_tools" in phase5
    assert "runtime_profile.config" in phase5
    assert "transport-focused" in phase5
    assert "summary, revert, and unrevert" in phase5
