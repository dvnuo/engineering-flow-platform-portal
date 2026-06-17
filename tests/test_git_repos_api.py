from types import SimpleNamespace

from app.api import git_repos


def test_list_git_repo_branches_parses_remote_heads(monkeypatch):
    captured = {}

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
    assert body == {
        "repo_url": "https://github.com/Acme/Agents.git",
        "branches": ["feature/persona", "master"],
    }
