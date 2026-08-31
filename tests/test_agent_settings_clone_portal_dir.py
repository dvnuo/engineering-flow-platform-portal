"""The behavior-pack init container must deliver portal/ into the workspace.

Portal serves the greeting and starter cards from `<workspace>/portal/` via the
runtime's /api/personalization. If the init container does not copy that
directory, personalization silently returns nothing in a real deployment while
still looking fine in local development, where the files are read straight from
a repository checkout.

These tests run the generated shell for real against a fake checkout, so the
copy is verified rather than assumed.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.k8s_service import K8sService


pytestmark = pytest.mark.skipif(
    shutil.which("sh") is None, reason="POSIX shell not available"
)


def _shell_command(workspace_dir: str) -> str:
    service = K8sService.__new__(K8sService)
    return service._agent_settings_git_clone_shell_command(workspace_dir)


def _run_copy_stage(tmp_path: Path, *, include_portal: bool) -> Path:
    """Execute only the copy stage against a prepared SOURCE_DIR."""

    source = tmp_path / "checkout"
    (source / "instructions").mkdir(parents=True)
    (source / "AGENTS.md").write_text("# Agent\n", encoding="utf-8")
    (source / "instructions" / "overview.instructions.md").write_text("x\n", encoding="utf-8")
    if include_portal:
        (source / "portal").mkdir()
        (source / "portal" / "welcome.md").write_text("Hello there.\n", encoding="utf-8")
        (source / "portal" / "cards.yaml").write_text("cards: []\n", encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    full = _shell_command(str(workspace).replace("\\", "/"))
    # Drop everything up to and including the clone; the remainder is the
    # validation and copy logic under test.
    lines = full.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("SOURCE_DIR="))
    body = "\n".join(lines[start:])
    script = f'set -eu\nSOURCE_DIR="{str(source).replace(chr(92), "/")}"\n' + "\n".join(
        line for line in body.splitlines() if not line.startswith("SOURCE_DIR=")
    )

    # The script defaults this above the slice we execute, so supply it here.
    env = {**os.environ, "AGENT_SETTINGS_REPO_SUBDIR": ""}
    result = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    return workspace


def test_portal_directory_reaches_the_workspace(tmp_path):
    workspace = _run_copy_stage(tmp_path, include_portal=True)

    assert (workspace / "portal" / "welcome.md").read_text(encoding="utf-8").strip() == "Hello there."
    assert (workspace / "portal" / "cards.yaml").exists()


def test_existing_instructions_and_agents_still_land(tmp_path):
    workspace = _run_copy_stage(tmp_path, include_portal=True)

    assert (workspace / "AGENTS.md").exists()
    assert (workspace / "instructions" / "overview.instructions.md").exists()
    assert (workspace / ".efp" / "instructions" / "overview.instructions.md").exists()


def test_a_branch_without_portal_still_boots(tmp_path):
    # Behavior-pack branches predating personalization must keep working;
    # Portal falls back to its generic welcome.
    workspace = _run_copy_stage(tmp_path, include_portal=False)

    assert (workspace / "AGENTS.md").exists()
    assert not (workspace / "portal").exists()


def test_a_stale_portal_directory_is_replaced(tmp_path):
    # The workspace persists across restarts on a PVC, so a leftover portal/
    # from a previous branch must not survive a switch.
    source = tmp_path / "checkout"
    (source / "instructions").mkdir(parents=True)
    (source / "AGENTS.md").write_text("# Agent\n", encoding="utf-8")
    (source / "instructions" / "a.md").write_text("x\n", encoding="utf-8")
    (source / "portal").mkdir()
    (source / "portal" / "welcome.md").write_text("New greeting.\n", encoding="utf-8")

    workspace = tmp_path / "workspace"
    (workspace / "portal").mkdir(parents=True)
    (workspace / "portal" / "stale.yaml").write_text("old\n", encoding="utf-8")

    full = _shell_command(str(workspace).replace("\\", "/"))
    lines = full.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("SOURCE_DIR="))
    body = "\n".join(
        line for line in lines[start:] if not line.startswith("SOURCE_DIR=")
    )
    script = f'set -eu\nSOURCE_DIR="{str(source).replace(chr(92), "/")}"\n' + body

    # The script defaults this above the slice we execute, so supply it here.
    env = {**os.environ, "AGENT_SETTINGS_REPO_SUBDIR": ""}
    result = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr

    assert (workspace / "portal" / "welcome.md").read_text(encoding="utf-8").strip() == "New greeting."
    assert not (workspace / "portal" / "stale.yaml").exists()
