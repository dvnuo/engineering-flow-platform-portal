import subprocess

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.deps import get_current_user
from app.redaction import sanitize_exception_message
from app.utils.git_urls import normalize_git_repo_url


router = APIRouter(prefix="/api/git-repos", tags=["git-repos"])


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
        result = subprocess.run(
            ["git", "ls-remote", "--heads", normalized_repo_url],
            check=False,
            capture_output=True,
            text=True,
            timeout=12,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Timed out while loading repository branches") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to load repository branches: {sanitize_exception_message(exc)}",
        ) from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git ls-remote failed").strip()
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
