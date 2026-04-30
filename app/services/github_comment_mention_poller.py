from __future__ import annotations

import re
from fnmatch import fnmatchcase
from datetime import datetime, timedelta

import httpx

from app.services.provider_config_resolver import GithubProviderConfig

MENTION_RE = re.compile(r"(?<![A-Za-z0-9_.-])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)(?![A-Za-z0-9_-])")
SURFACE_ENDPOINTS = {
    "issue_comment": "issues/comments",
    "pull_request_review_comment": "pulls/comments",
    "commit_comment": "comments",
}


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _iso_z(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat() + "Z"


def is_efp_auto_reply(body: str) -> bool:
    return "<!-- efp:auto-reply" in (body or "")


def extract_github_mentions(body: str, strip_code_blocks: bool = True) -> list[str]:
    text = body or ""
    lines = []
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if strip_code_blocks and in_code:
            continue
        if line.lstrip().startswith(">"):
            continue
        lines.append(line)
    found = [m.group(1).lower() for m in MENTION_RE.finditer("\n".join(lines))]
    return list(dict.fromkeys(found))


def _parse_num_from_url(raw: str | None, patterns: list[str]) -> int | None:
    text = str(raw or "")
    for p in patterns:
        m = re.search(p, text)
        if m:
            return int(m.group(1))
    return None


class GithubCommentMentionPoller:
    async def poll_mentions(self, *, provider_config: GithubProviderConfig, owner: str, repo: str, mention_target: str, since_by_surface: dict, surfaces: list[str], overlap_seconds: int = 120, max_pages_per_surface: int = 10, initial_since: datetime | None = None, ignore_self_comments: bool = True, ignore_bot_comments: bool = True, ignore_efp_auto_reply_marker: bool = True, strip_code_blocks_before_matching: bool = True) -> tuple[list[dict], dict]:
        base_url = provider_config.base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {provider_config.api_token}", "Accept": "application/vnd.github+json"}
        poll_started_at = datetime.utcnow()
        items = []
        poll_cursors = {}
        async with httpx.AsyncClient(timeout=20) as client:
            for surface in surfaces:
                endpoint = SURFACE_ENDPOINTS.get(surface)
                if not endpoint:
                    raise ValueError(f"Unsupported GitHub comment mention surface: {surface}")
                last_dt = _parse_dt((since_by_surface.get(surface) or {}).get("last_seen_updated_at"))
                query_since = (last_dt - timedelta(seconds=overlap_seconds)) if last_dt else (initial_since or poll_started_at)
                max_seen_dt = query_since
                max_seen_id = int((since_by_surface.get(surface) or {}).get("last_seen_comment_id") or 0)
                hit_page_limit = False
                for page in range(1, max_pages_per_surface + 1):
                    params = {"since": _iso_z(query_since), "per_page": 100, "page": page}
                    if surface in {"issue_comment", "pull_request_review_comment"}:
                        params.update({"sort": "updated", "direction": "asc"})
                    resp = await client.get(f"{base_url}/repos/{owner}/{repo}/{endpoint}", params=params, headers=headers)
                    if resp.status_code >= 400:
                        raise ValueError(f"GitHub API error surface={surface} status={resp.status_code}")
                    batch = resp.json() or []
                    for c in batch:
                        upd = _parse_dt(c.get("updated_at")) or poll_started_at
                        cid = int(c.get("id") or 0)
                        if upd > max_seen_dt or (upd == max_seen_dt and cid > max_seen_id):
                            max_seen_dt = upd
                            max_seen_id = cid
                        body = c.get("body") or ""
                        mentioned = extract_github_mentions(body, strip_code_blocks_before_matching)
                        target = mention_target.lower()
                        if target not in mentioned:
                            continue
                        author = ((c.get("user") or {}).get("login") or "").lower()
                        if ignore_self_comments and author == target:
                            continue
                        user_type = str((c.get("user") or {}).get("type") or "").strip().lower()
                        if ignore_bot_comments and user_type == "bot":
                            continue
                        if ignore_efp_auto_reply_marker and is_efp_auto_reply(body):
                            continue
                        item = self._normalize(surface, c, owner, repo, target, mentioned)
                        items.append(item)
                    if len(batch) < 100:
                        break
                    if page == max_pages_per_surface and len(batch) >= 100:
                        hit_page_limit = True
                cursor_dt = max_seen_dt if hit_page_limit else max(max_seen_dt, poll_started_at)
                poll_cursors[surface] = {"last_seen_updated_at": _iso_z(cursor_dt), "last_seen_comment_id": max_seen_id}
        return items, {"poll_cursors": poll_cursors}

    async def list_org_repositories(self, *, provider_config: GithubProviderConfig, org: str, repo_selector: dict | None = None, max_pages: int = 10) -> list[dict]:
        base_url = provider_config.base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {provider_config.api_token}", "Accept": "application/vnd.github+json"}
        selector = repo_selector if isinstance(repo_selector, dict) else {}
        include_patterns = [str(x) for x in (selector.get("include") or []) if str(x).strip()]
        exclude_patterns = [str(x) for x in (selector.get("exclude") or []) if str(x).strip()]
        include_archived = bool(selector.get("include_archived", False))
        include_forks = bool(selector.get("include_forks", False))
        output: list[dict] = []
        async with httpx.AsyncClient(timeout=20) as client:
            for page in range(1, max_pages + 1):
                resp = await client.get(f"{base_url}/orgs/{org}/repos", params={"per_page": 100, "page": page, "type": "all"}, headers=headers)
                if resp.status_code >= 400:
                    raise ValueError(f"GitHub API error org={org} status={resp.status_code}")
                batch = resp.json() or []
                for repo in batch:
                    name = str(repo.get("name") or "").strip()
                    if not name:
                        continue
                    if repo.get("archived") and not include_archived:
                        continue
                    if repo.get("fork") and not include_forks:
                        continue
                    if include_patterns and not any(fnmatchcase(name, p) for p in include_patterns):
                        continue
                    if exclude_patterns and any(fnmatchcase(name, p) for p in exclude_patterns):
                        continue
                    output.append({"owner": org, "repo": name, "full_name": repo.get("full_name") or f"{org}/{name}", "archived": repo.get("archived"), "fork": repo.get("fork")})
                if len(batch) < 100:
                    break
        return output

    def _normalize(self, surface: str, c: dict, owner: str, repo: str, mention_target: str, mentioned: list[str]) -> dict:
        if surface == "issue_comment":
            issue_number = _parse_num_from_url(c.get("issue_url"), [r"/issues/(\d+)$"]) or _parse_num_from_url(c.get("html_url"), [r"/issues/(\d+)", r"/pull/(\d+)"]) or 0
            html_url = c.get("html_url") or ""
            is_pr = "/pull/" in html_url
            return {
                "source_kind": "github.mention", "source_event": "poll.issue_comment", "comment_kind": "issue_comment",
                "context_type": "pull_request" if is_pr else "issue", "owner": owner, "repo": repo, "issue_number": issue_number,
                "pull_number": issue_number if is_pr else None, "comment_id": c.get("id"), "node_id": c.get("node_id"), "body": c.get("body"),
                "author": (c.get("user") or {}).get("login"), "author_type": (c.get("user") or {}).get("type"), "author_association": c.get("author_association"),
                "html_url": html_url, "api_url": c.get("url"), "created_at": c.get("created_at"), "updated_at": c.get("updated_at"),
                "mentioned_account": mention_target, "mentioned_logins": mentioned, "source_payload": c,
            }
        if surface == "commit_comment":
            return {
                "source_kind": "github.mention", "source_event": "poll.commit_comment", "comment_kind": "commit_comment",
                "context_type": "commit", "owner": owner, "repo": repo, "commit_id": c.get("commit_id"), "commit_sha": c.get("commit_id"),
                "comment_id": c.get("id"), "node_id": c.get("node_id"), "body": c.get("body"), "author": (c.get("user") or {}).get("login"),
                "author_type": (c.get("user") or {}).get("type"), "html_url": c.get("html_url"), "api_url": c.get("url"), "path": c.get("path"),
                "line": c.get("line"), "position": c.get("position"), "created_at": c.get("created_at"), "updated_at": c.get("updated_at"),
                "mentioned_account": mention_target, "mentioned_logins": mentioned, "source_payload": c,
            }
        pull_url = c.get("pull_request_url") or ""
        pull_number = _parse_num_from_url(pull_url, [r"/pulls/(\d+)$"]) or _parse_num_from_url(c.get("html_url"), [r"/pull/(\d+)"]) or 0
        return {
            "source_kind": "github.mention", "source_event": "poll.pull_request_review_comment", "comment_kind": "pull_request_review_comment",
            "context_type": "pull_request_review_thread", "owner": owner, "repo": repo, "pull_number": pull_number, "issue_number": pull_number,
            "comment_id": c.get("id"), "review_comment_id": c.get("id"), "in_reply_to_id": c.get("in_reply_to_id"), "body": c.get("body"),
            "author": (c.get("user") or {}).get("login"), "author_type": (c.get("user") or {}).get("type"), "author_association": c.get("author_association"),
            "html_url": c.get("html_url"), "api_url": c.get("url"), "path": c.get("path"), "line": c.get("line"), "side": c.get("side"),
            "diff_hunk": c.get("diff_hunk"), "created_at": c.get("created_at"), "updated_at": c.get("updated_at"), "mentioned_account": mention_target,
            "mentioned_logins": mentioned, "source_payload": c,
        }
