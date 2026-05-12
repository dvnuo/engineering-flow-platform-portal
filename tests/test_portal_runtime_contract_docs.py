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


    readme = Path("README.md").read_text(encoding="utf-8")
    assert "docs/PORTAL_RUNTIME_CONTRACT.md" in readme
    assert "native runtime clones runtime repo + skill repo via initContainers" not in readme
    assert "DEFAULT_OPENCODE_PERMISSION_MODE" in readme
    assert "DEFAULT_OPENCODE_ALLOW_BASH_ALL" in readme
    assert "workspace_full_access" in readme
    assert "EFP_WORKSPACE_REPOS_DIR=/workspace/repos" in readme
    assert "EFP_GIT_CHECKOUT_TIMEOUT_SECONDS=120" in readme
