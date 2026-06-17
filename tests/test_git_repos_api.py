import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import git_repos


def test_list_git_repo_branches_parses_remote_heads(monkeypatch):
    captured = {}
    monkeypatch.setattr(git_repos.settings, "git_repo_auth_pat", "")
    monkeypatch.setattr(git_repos.settings, "git_repo_ls_remote_timeout_seconds", 12)
    monkeypatch.setenv("GIT_ASKPASS", "host-askpass-should-not-leak")

    def _run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "abc123\trefs/heads/master\n"
                "def456\trefs/heads/feature/persona\n"
                "ghi789\trefs/tags/v1\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(git_repos.subprocess, "run", _run)

    body = git_repos.list_git_repo_branches(
        repo_url="git@github.com:Acme/Agents.git",
        user=SimpleNamespace(id=1),
    )

    assert captured["args"] == [
        "git",
        "ls-remote",
        "--heads",
        "https://github.com/Acme/Agents.git",
    ]
    assert captured["kwargs"]["timeout"] == 12
    assert captured["kwargs"]["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert "GIT_ASKPASS" not in captured["kwargs"]["env"]
    assert "GIT_REPO_AUTH_PAT" not in captured["kwargs"]["env"]
    assert body == {
        "repo_url": "https://github.com/Acme/Agents.git",
        "branches": ["feature/persona", "master"],
    }


def test_list_git_repo_branches_uses_configured_pat(monkeypatch):
    captured = {}
    monkeypatch.setattr(git_repos.settings, "git_repo_auth_username", "portal-bot")
    monkeypatch.setattr(git_repos.settings, "git_repo_auth_pat", "secret-pat")
    monkeypatch.setattr(git_repos.settings, "git_repo_ls_remote_timeout_seconds", 7)

    def _run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="abc123\trefs/heads/master\n", stderr="")

    monkeypatch.setattr(git_repos.subprocess, "run", _run)

    body = git_repos.list_git_repo_branches(
        repo_url="https://github.com/Acme/Agents.git",
        user=SimpleNamespace(id=1),
    )

    env = captured["kwargs"]["env"]
    askpass_path = env["GIT_ASKPASS"]
    assert captured["args"] == ["git", "ls-remote", "--heads", "https://github.com/Acme/Agents.git"]
    assert "secret-pat" not in " ".join(captured["args"])
    assert captured["kwargs"]["timeout"] == 7
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_REPO_AUTH_USERNAME"] == "portal-bot"
    assert env["GIT_REPO_AUTH_PAT"] == "secret-pat"
    assert askpass_path
    assert not os.path.exists(askpass_path)
    assert body == {"repo_url": "https://github.com/Acme/Agents.git", "branches": ["master"]}


def test_list_git_repo_branches_redacts_configured_pat_on_failure(monkeypatch):
    monkeypatch.setattr(git_repos.settings, "git_repo_auth_pat", "secret-pat")

    def _run(args, **kwargs):
        return SimpleNamespace(returncode=128, stdout="", stderr="fatal: authentication failed for secret-pat\n")

    monkeypatch.setattr(git_repos.subprocess, "run", _run)

    with pytest.raises(HTTPException) as exc_info:
        git_repos.list_git_repo_branches(
            repo_url="https://github.com/Acme/Agents.git",
            user=SimpleNamespace(id=1),
        )

    assert exc_info.value.status_code == 502
    assert "secret-pat" not in exc_info.value.detail
    assert "[REDACTED]" in exc_info.value.detail
