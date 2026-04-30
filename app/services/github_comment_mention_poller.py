from __future__ import annotations

import re
from datetime import datetime, timedelta

import httpx

from app.services.provider_config_resolver import GithubProviderConfig

MENTION_RE = re.compile(r"(?<![A-Za-z0-9_.-])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)(?![A-Za-z0-9_-])")


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


class GithubCommentMentionPoller:
    async def poll_mentions(self, *, provider_config: GithubProviderConfig, owner: str, repo: str, mention_target: str, since_by_surface: dict, surfaces: list[str], overlap_seconds: int = 120, max_pages_per_surface: int = 10, initial_since: datetime | None = None, ignore_self_comments: bool = True, ignore_bot_comments: bool = True, ignore_efp_auto_reply_marker: bool = True, strip_code_blocks_before_matching: bool = True) -> tuple[list[dict], dict]:
        base_url = provider_config.base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {provider_config.api_token}", "Accept": "application/vnd.github+json"}
        now = datetime.utcnow()
        items = []
        poll_cursors = {}
        async with httpx.AsyncClient(timeout=20) as client:
            for surface in surfaces:
                endpoint = "issues/comments" if surface == "issue_comment" else "pulls/comments"
                last_dt = _parse_dt((since_by_surface.get(surface) or {}).get("last_seen_updated_at"))
                query_since = (last_dt - timedelta(seconds=overlap_seconds)) if last_dt else (initial_since or now)
                max_seen_dt = query_since
                max_seen_id = int((since_by_surface.get(surface) or {}).get("last_seen_comment_id") or 0)
                for page in range(1, max_pages_per_surface + 1):
                    params = {"sort": "updated", "direction": "asc", "since": _iso_z(query_since), "per_page": 100, "page": page}
                    resp = await client.get(f"{base_url}/repos/{owner}/{repo}/{endpoint}", params=params, headers=headers)
                    if resp.status_code >= 400:
                        raise ValueError(f"GitHub API error surface={surface} status={resp.status_code}")
                    batch = resp.json() or []
                    for c in batch:
                        body = c.get("body") or ""
                        mentioned = extract_github_mentions(body, strip_code_blocks_before_matching)
                        target = mention_target.lower()
                        if target not in mentioned:
                            continue
                        author = ((c.get("user") or {}).get("login") or "").lower()
                        if ignore_self_comments and author == target:
                            continue
                        if ignore_bot_comments and str((c.get("user") or {}).get("type") or "") == "Bot":
                            continue
                        if ignore_efp_auto_reply_marker and is_efp_auto_reply(body):
                            continue
                        item = self._normalize(surface, c, owner, repo, target, mentioned)
                        items.append(item)
                        upd = _parse_dt(c.get("updated_at")) or now
                        if upd >= max_seen_dt:
                            max_seen_dt = upd
                            max_seen_id = max(max_seen_id, int(c.get("id") or 0))
                    if len(batch) < 100:
                        break
                poll_cursors[surface] = {"last_seen_updated_at": _iso_z(max_seen_dt if max_seen_dt else now), "last_seen_comment_id": max_seen_id}
        return items, {"poll_cursors": poll_cursors}

    def _normalize(self, surface: str, c: dict, owner: str, repo: str, mention_target: str, mentioned: list[str]) -> dict:
        if surface == "issue_comment":
            issue_number = int(str(c.get("issue_url") or "0").rstrip("/").split("/")[-1])
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
        pull_url = c.get("pull_request_url") or ""
        pull_number = int(str(pull_url).rstrip("/").split("/")[-1]) if pull_url else int(re.search(r"/pull/(\d+)", c.get("html_url") or "").group(1))
        return {
            "source_kind": "github.mention", "source_event": "poll.pull_request_review_comment", "comment_kind": "pull_request_review_comment",
            "context_type": "pull_request_review_thread", "owner": owner, "repo": repo, "pull_number": pull_number, "issue_number": pull_number,
            "comment_id": c.get("id"), "review_comment_id": c.get("id"), "in_reply_to_id": c.get("in_reply_to_id"), "body": c.get("body"),
            "author": (c.get("user") or {}).get("login"), "author_type": (c.get("user") or {}).get("type"), "author_association": c.get("author_association"),
            "html_url": c.get("html_url"), "api_url": c.get("url"), "path": c.get("path"), "line": c.get("line"), "side": c.get("side"),
            "diff_hunk": c.get("diff_hunk"), "created_at": c.get("created_at"), "updated_at": c.get("updated_at"), "mentioned_account": mention_target,
            "mentioned_logins": mentioned, "source_payload": c,
        }
