import os
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.config import get_settings
from app.deps import get_current_user
from app.redaction import sanitize_exception_message
from app.utils.git_urls import normalize_git_repo_url


router = APIRouter(prefix="/api/git-repos", tags=["git-repos"])
settings = get_settings()


@contextmanager
def _temporary_git_askpass() -> Iterator[str | None]:
    token = _git_repo_auth_pat()
    if not token:
        yield None
        return

    suffix = ".bat" if os.name == "nt" else ".sh"
    handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=suffix, delete=False)
    try:
        path = handle.name
        if os.name == "nt":
            handle.write("@echo off\n")
            handle.write('echo %~1 | findstr /I "Username" >nul\n')
            handle.write("if %ERRORLEVEL%==0 (\n")
            handle.write("  echo %GIT_REPO_AUTH_USERNAME%\n")
            handle.write(") else (\n")
            handle.write("  echo %GIT_REPO_AUTH_PAT%\n")
            handle.write(")\n")
        else:
            handle.write("#!/bin/sh\n")
            handle.write('case "$1" in\n')
            handle.write('  *Username*|*username*) printf "%s\\n" "${GIT_REPO_AUTH_USERNAME}" ;;\n')
            handle.write('  *) printf "%s\\n" "${GIT_REPO_AUTH_PAT}" ;;\n')
            handle.write("esac\n")
        handle.close()
        if os.name != "nt":
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        yield path
    finally:
        if not handle.closed:
            handle.close()
        try:
            os.remove(handle.name)
        except OSError:
            pass


def _git_repo_auth_username() -> str:
    return (settings.git_repo_auth_username or "x-access-token").strip() or "x-access-token"


def _git_repo_auth_pat() -> str:
    return (settings.git_repo_auth_pat or "").strip()


def _git_ls_remote_timeout_seconds() -> int:
    return max(1, int(settings.git_repo_ls_remote_timeout_seconds or 12))


def _build_git_ls_remote_env(askpass_path: str | None) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if not askpass_path:
        env.pop("GIT_ASKPASS", None)
        env.pop("GIT_REPO_AUTH_USERNAME", None)
        env.pop("GIT_REPO_AUTH_PAT", None)
        return env

    env["GIT_ASKPASS"] = askpass_path
    env["GIT_REPO_AUTH_USERNAME"] = _git_repo_auth_username()
    env["GIT_REPO_AUTH_PAT"] = _git_repo_auth_pat()
    return env


def _sanitize_git_ls_remote_message(value) -> str:
    message = sanitize_exception_message(value)
    token = _git_repo_auth_pat()
    if token:
        message = message.replace(token, "[REDACTED]")
    return message


@router.get("/branches")
def list_git_repo_branches(
    repo_url: str = Query(..., min_length=1),
    user=Depends(get_current_user),
):
    _ = user
    normalized_repo_url = normalize_git_repo_url(repo_url)
    if not normalized_repo_url:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="repo_url is required")

    try:
        with _temporary_git_askpass() as askpass_path:
            result = subprocess.run(
                ["git", "ls-remote", "--heads", normalized_repo_url],
                check=False,
                capture_output=True,
                text=True,
                timeout=_git_ls_remote_timeout_seconds(),
                env=_build_git_ls_remote_env(askpass_path),
            )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Timed out while loading repository branches") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to load repository branches: {_sanitize_git_ls_remote_message(exc)}",
        ) from exc

    if result.returncode != 0:
        detail = _sanitize_git_ls_remote_message(result.stderr or result.stdout or "git ls-remote failed").strip()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to load repository branches: {detail[:300]}",
        )

    branches: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) != 2:
            continue
        ref = parts[1]
        prefix = "refs/heads/"
        if ref.startswith(prefix):
            branches.append(ref[len(prefix):])

    return {"repo_url": normalized_repo_url, "branches": sorted(set(branches))}
