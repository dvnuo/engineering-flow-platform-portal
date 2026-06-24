from pathlib import Path


def test_portal_runtime_contract_doc_and_readme_alignment():
    doc_path = Path("docs/PORTAL_RUNTIME_CONTRACT.md")
    assert doc_path.exists()
    text = doc_path.read_text(encoding="utf-8")
    assert "/a/{agent_id}/api/*" in text
    assert "/app/skills" in text
    assert "/app/tools" not in text
    assert "runtime source overlay" in text
    assert "ENABLE_RUNTIME_SOURCE_OVERLAY" not in text
    assert "DEFAULT_AGENT_RUNTIME_REPO_URL" not in text
    assert "DEFAULT_AGENT_RUNTIME_BRANCH" not in text
    assert ("runtime" + "_v2") not in text
    assert ("runtime" + " v2") not in text
    assert "X-Trace-Id" in text
    assert "runtime_type" in text
    assert "/home/opencode" not in text
    assert "On-demand repository checkout contract" in text
    assert "Portal supports two runtime markers: `native` and `opencode`." in text
    assert "`/api/agents/defaults` returns `default_runtime_type` and a `runtime_types` matrix" in text
    assert "Missing, blank, or legacy stored runtime markers normalize to `native`" in text
    assert "Invalid non-empty request values are rejected at request boundaries." in text
    assert "Switching an existing agent between `native` and `opencode` is allowed." in text
    assert "Both runtimes default to `/workspace`" in text
    assert "OpenCode-specific state, env, and mount wiring" in text
    assert "multiple Python EFP versions" in text
    assert "advanced runtime/source settings controls" in text
    assert "`GIT_TOKEN` remains initContainer-only" in text

    assert "runtime/tools index" not in text
    assert "tools repo/runtime tools index" not in text
    assert "tools-index" not in text
    assert "tools_index" not in text
    assert "EFP_TOOLS_DIR" not in text
    assert "OPENCODE_TOOLS_DIR" not in text
    assert "Portal provisions skills/tools" not in text
    assert "Portal does not configure external tools repositories, branches, or mounts." in text
    assert "Runtime built-in tools are runtime-owned." in text
    assert "does not clone runtime source into `/app/src` or `/app/.git`" in text
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
    assert "tools, skills, loop control, context shaping, compaction" in text
    assert "runtime mode" in text
    assert "advanced runtime controls" not in text
    assert "runtime_profile.config" in text
    assert "LLM provider/model/Copilot API key fields" in text
    assert "mobile BrowserStack config" in text
    assert "BrowserStackLocal" in text
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
    assert "ENABLE_RUNTIME_SOURCE_OVERLAY" not in readme
    assert "DEFAULT_AGENT_RUNTIME_REPO_URL" not in readme
    assert "DEFAULT_AGENT_RUNTIME_BRANCH" not in readme
    assert ("runtime" + "_v2") not in readme
    assert ("runtime" + " v2") not in readme
    assert "native runtime clones runtime repo + skill repo via initContainers" not in readme
    assert ("single Python EFP" + " runtime") not in readme
    assert ("no runtime" + " selector") not in readme
    assert "Portal provisions either the Python EFP native runtime or the OpenCode runtime based on `runtime_type`." in readme
    assert "`/api/agents/defaults` exposes `default_runtime_type` and the supported `runtime_types` matrix" in readme
    assert "New agents mount `/workspace` by default for both runtimes." in readme
    assert "no alternate Python EFP runtime versions" in readme
    assert "no runtime source overlay" in readme
    assert "no runtime/source settings surface" in readme
    assert "Portal remains the control plane; runtime owns tools, skills execution, loop control, context shaping, compaction, sessions, permissions" in readme

    phase5 = Path("docs/PHASE5_PRODUCTIZATION.md").read_text(encoding="utf-8")
    assert "Runtime capability snapshots" in phase5
    assert "apply_patch" in phase5
    assert "todowrite" in phase5
    assert "Runtime profile apply/config contract" in phase5
    assert "runtime_profile.config" in phase5
    assert "Portal-owned profile context" in phase5
    assert "Mobile/BrowserStack" in phase5
    assert "Python EFP runtime" in phase5
    assert "transport-focused" not in phase5
    assert "summary, revert, and unrevert" in phase5
