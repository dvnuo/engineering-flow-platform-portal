from __future__ import annotations

import re
from fnmatch import fnmatchcase
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx

from app.services.provider_config_resolver import GithubProviderConfig

MENTION_RE = re.compile(r"(?<![A-Za-z0-9_.-])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)(?![A-Za-z0-9_-])")
SURFACE_CONFIG = {
    "issue_comment": {"endpoint": "issues/comments", "supports_since": True, "supports_updated_sort": True},
    "pull_request_review_comment": {"endpoint": "pulls/comments", "supports_since": True, "supports_updated_sort": True},
    "commit_comment": {"endpoint": "comments", "supports_since": False, "supports_updated_sort": False, "ordered_by": "ascending_id"},
    "discussion_comment": {"endpoint": None, "graphql": True, "supports_since": False, "supports_updated_sort": False},
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


def _parse_last_page_from_link_header(link_header: str | None) -> int | None:
    if not link_header:
        return None
    for part in str(link_header).split(","):
        if 'rel="last"' not in part:
            continue
        seg = part.split(";")[0].strip()
        if seg.startswith("<") and seg.endswith(">"):
            seg = seg[1:-1]
        query = parse_qs(urlparse(seg).query)
        val = query.get("page", [None])[0]
        if val is not None:
            try:
                return int(val)
            except ValueError:
                return None
    return None


class GithubCommentMentionPoller:
    @staticmethod
    def _github_graphql_url(base_url: str) -> str:
        normalized = base_url.rstrip("/")
        if normalized.endswith("/api/v3"):
            return normalized[:-7] + "/api/graphql"
        if normalized == "https://api.github.com":
            return "https://api.github.com/graphql"
        return "https://api.github.com/graphql"

    async def _graphql_request(self, client, *, base_url: str, headers: dict, query: str, variables: dict | None) -> dict:
        resp = await client.post(self._github_graphql_url(base_url), headers=headers, json={"query": query, "variables": variables or {}})
        if resp.status_code >= 400:
            raise ValueError(f"GitHub GraphQL API error status={resp.status_code}")
        body = resp.json() or {}
        if body.get("errors"):
            first = body["errors"][0]
            msg = first.get("message") if isinstance(first, dict) else str(first)
            raise ValueError(f"GitHub GraphQL error: {msg}")
        return body.get("data") or {}

    async def poll_mentions(self, *, provider_config: GithubProviderConfig, owner: str, repo: str, mention_target: str, since_by_surface: dict, surfaces: list[str], overlap_seconds: int = 120, max_pages_per_surface: int = 10, initial_since: datetime | None = None, ignore_self_comments: bool = True, ignore_bot_comments: bool = True, ignore_efp_auto_reply_marker: bool = True, strip_code_blocks_before_matching: bool = True, commit_comment_initial_tail_pages: int = 2, max_discussion_pages_per_run: int = 5, discussion_comments_tail_count: int = 100, discussion_replies_tail_count: int = 50) -> tuple[list[dict], dict]:
        base_url = provider_config.base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {provider_config.api_token}", "Accept": "application/vnd.github+json"}
        poll_started_at = datetime.utcnow()
        items = []
        poll_cursors = {}
        async with httpx.AsyncClient(timeout=20) as client:
            for surface in surfaces:
                cfg = SURFACE_CONFIG.get(surface)
                if not cfg:
                    raise ValueError(f"Unsupported GitHub comment mention surface: {surface}")
                if surface == "discussion_comment":
                    d_items, d_cursor = await self._poll_discussion_comments_for_repo(
                        client=client, base_url=base_url, headers=headers, owner=owner, repo=repo, mention_target=mention_target,
                        cursor=since_by_surface.get(surface) or {}, initial_since=initial_since, overlap_seconds=overlap_seconds,
                        ignore_self_comments=ignore_self_comments, ignore_bot_comments=ignore_bot_comments,
                        ignore_efp_auto_reply_marker=ignore_efp_auto_reply_marker, strip_code_blocks_before_matching=strip_code_blocks_before_matching,
                        max_discussion_pages_per_run=max_discussion_pages_per_run, discussion_comments_tail_count=discussion_comments_tail_count, discussion_replies_tail_count=discussion_replies_tail_count,
                    )
                    items.extend(d_items)
                    poll_cursors[surface] = d_cursor
                    continue
                endpoint = cfg["endpoint"]
                last_dt = _parse_dt((since_by_surface.get(surface) or {}).get("last_seen_updated_at"))
                query_since = (last_dt - timedelta(seconds=overlap_seconds)) if last_dt else (initial_since or poll_started_at)
                max_seen_dt = query_since
                max_seen_id = int((since_by_surface.get(surface) or {}).get("last_seen_comment_id") or 0)
                hit_page_limit = False
                start_page = 1
                if surface == "commit_comment":
                    cursor = since_by_surface.get(surface) or {}
                    if cursor.get("next_scan_page") or cursor.get("last_seen_page"):
                        start_page = max(1, int(cursor.get("next_scan_page") or cursor.get("last_seen_page") or 1))
                page_where_max_seen = start_page
                last_page_seen = int((since_by_surface.get(surface) or {}).get("last_seen_total_pages") or 0)
                for idx in range(max_pages_per_surface):
                    page = start_page + idx
                    params = {"per_page": 100, "page": page}
                    if cfg.get("supports_since"):
                        params["since"] = _iso_z(query_since)
                    if cfg.get("supports_updated_sort"):
                        params.update({"sort": "updated", "direction": "asc"})
                    resp = await client.get(f"{base_url}/repos/{owner}/{repo}/{endpoint}", params=params, headers=headers)
                    if resp.status_code >= 400:
                        raise ValueError(f"GitHub API error surface={surface} status={resp.status_code}")
                    batch = resp.json() or []
                    last_from_header = _parse_last_page_from_link_header((getattr(resp, "headers", {}) or {}).get("Link"))
                    if last_from_header:
                        last_page_seen = last_from_header
                    if surface == "commit_comment" and idx == 0 and not (since_by_surface.get(surface) or {}).get("last_seen_comment_id") and last_page_seen:
                        effective_tail_pages = min(max(1, commit_comment_initial_tail_pages), max(1, max_pages_per_surface))
                        start_page = max(1, last_page_seen - effective_tail_pages + 1)
                        page = start_page
                        params = {"per_page": 100, "page": page}
                        resp = await client.get(f"{base_url}/repos/{owner}/{repo}/{endpoint}", params=params, headers=headers)
                        if resp.status_code >= 400:
                            raise ValueError(f"GitHub API error surface={surface} status={resp.status_code}")
                        batch = resp.json() or []
                        last_from_header = _parse_last_page_from_link_header((getattr(resp, "headers", {}) or {}).get("Link"))
                        if last_from_header:
                            last_page_seen = last_from_header
                    for c in batch:
                        upd = _parse_dt(c.get("updated_at")) or _parse_dt(c.get("created_at")) or poll_started_at
                        cid = int(c.get("id") or 0)
                        if upd > max_seen_dt or (upd == max_seen_dt and cid > max_seen_id):
                            max_seen_dt = upd
                            max_seen_id = cid
                            page_where_max_seen = page
                        if surface == "commit_comment":
                            last_seen_id = int((since_by_surface.get(surface) or {}).get("last_seen_comment_id") or 0)
                            is_new_enough = ((last_seen_id > 0) and (cid > last_seen_id)) or ((last_seen_id == 0) and (upd >= query_since))
                            if not is_new_enough:
                                continue
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
                    is_last_allowed_page = idx == max_pages_per_surface - 1
                    has_more_by_batch_size = len(batch) >= 100
                    has_more_by_link = bool(last_page_seen and page < last_page_seen)
                    if is_last_allowed_page and has_more_by_batch_size and (not last_page_seen or has_more_by_link):
                        hit_page_limit = True
                cursor_dt = max_seen_dt if hit_page_limit else max(max_seen_dt, poll_started_at)
                poll_cursors[surface] = {"last_seen_updated_at": _iso_z(cursor_dt), "last_seen_comment_id": max_seen_id}
                if surface == "commit_comment":
                    next_scan_page = (start_page + max_pages_per_surface) if hit_page_limit else max(1, page_where_max_seen - 1)
                    poll_cursors[surface].update({"last_seen_page": page_where_max_seen, "next_scan_page": next_scan_page, "last_seen_total_pages": last_page_seen})
        return items, {"poll_cursors": poll_cursors}

    async def _poll_discussion_comments_for_repo(self, *, client, base_url: str, headers: dict, owner: str, repo: str, mention_target: str, cursor: dict, initial_since: datetime | None, overlap_seconds: int, max_discussion_pages_per_run: int = 5, discussion_comments_tail_count: int = 100, discussion_replies_tail_count: int = 50, ignore_self_comments: bool = True, ignore_bot_comments: bool = True, ignore_efp_auto_reply_marker: bool = True, strip_code_blocks_before_matching: bool = True) -> tuple[list[dict], dict]:
        last_dt = _parse_dt(cursor.get("last_seen_updated_at"))
        query_since = (last_dt - timedelta(seconds=overlap_seconds)) if last_dt else (initial_since or datetime.utcnow())
        gql = """query RepoDiscussions($owner:String!,$repo:String!,$after:String,$commentsLast:Int!,$repliesLast:Int!){repository(owner:$owner,name:$repo){discussions(first:25,after:$after,orderBy:{field:UPDATED_AT,direction:DESC}){pageInfo{hasNextPage endCursor} nodes{id number updatedAt comments(last:$commentsLast){nodes{id body url createdAt updatedAt authorAssociation author{login __typename} replyTo{id} replies(last:$repliesLast){nodes{id body url createdAt updatedAt authorAssociation author{login __typename} replyTo{id}}}}}}}}}"""
        items = []
        max_dt = last_dt or query_since
        max_id = str(cursor.get("last_seen_comment_id") or "")
        after = cursor.get("discussion_after_cursor")
        pages = 0
        hit_page_limit = False
        while pages < max_discussion_pages_per_run:
            data = await self._graphql_request(client, base_url=base_url, headers=headers, query=gql, variables={"owner": owner, "repo": repo, "after": after, "commentsLast": discussion_comments_tail_count, "repliesLast": discussion_replies_tail_count})
            discussions_obj = ((data.get("repository") or {}).get("discussions") or {})
            nodes = discussions_obj.get("nodes") or []
            page_info = discussions_obj.get("pageInfo") or {}
            pages += 1
            stop_after = False
            for d in nodes:
                dud = _parse_dt(d.get("updatedAt"))
                if dud and dud < query_since:
                    stop_after = True
                for c in ((d.get("comments") or {}).get("nodes") or []):
                    for node in [c] + (((c.get("replies") or {}).get("nodes") or [])):
                        upd = _parse_dt(node.get("updatedAt")) or _parse_dt(node.get("createdAt")) or query_since
                        if upd > max_dt:
                            max_dt = upd; max_id = str(node.get("id") or max_id)
                        if upd < query_since:
                            continue
                        body = node.get("body") or ""
                        mentioned = extract_github_mentions(body, strip_code_blocks_before_matching)
                        if mention_target.lower() not in mentioned:
                            continue
                        author_login = str(((node.get("author") or {}).get("login") or "")).lower()
                        author_type = str(((node.get("author") or {}).get("__typename") or "")).lower()
                        if ignore_self_comments and author_login == mention_target.lower():
                            continue
                        if ignore_bot_comments and author_type == "bot":
                            continue
                        if ignore_efp_auto_reply_marker and is_efp_auto_reply(body):
                            continue
                        items.append(self._normalize("discussion_comment", {"discussion": d, "comment": node}, owner, repo, mention_target.lower(), mentioned))
            if stop_after or not page_info.get("hasNextPage"):
                after = None
                break
            after = page_info.get("endCursor")
            if pages >= max_discussion_pages_per_run:
                hit_page_limit = True
                break
        cursor_dt = max_dt if hit_page_limit else max(max_dt, datetime.utcnow())
        return items, {"last_seen_updated_at": _iso_z(cursor_dt), "last_seen_comment_id": max_id, "discussion_after_cursor": after, "hit_page_limit": hit_page_limit}



    async def list_account_notifications(self, *, provider_config: GithubProviderConfig, since: datetime | None = None, reasons: list[str] | None = None, max_pages: int = 5) -> tuple[list[dict], dict]:
        base_url = provider_config.base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {provider_config.api_token}", "Accept": "application/vnd.github+json"}
        poll_started_at = datetime.utcnow()
        allowed_reasons = {str(x).strip() for x in (reasons or ["mention", "team_mention"]) if str(x).strip()}
        notifications: list[dict] = []
        max_updated = since or poll_started_at
        max_id = ""
        async with httpx.AsyncClient(timeout=20) as client:
            for page in range(1, max(1, max_pages) + 1):
                params = {"all": "true", "participating": "true", "per_page": 100, "page": page}
                if since:
                    params["since"] = _iso_z(since)
                resp = await client.get(f"{base_url}/notifications", params=params, headers=headers)
                if resp.status_code in {403, 404}:
                    raise ValueError("GitHub account notifications polling requires a user/PAT-compatible token")
                if resp.status_code >= 400:
                    raise ValueError(f"GitHub notifications API error status={resp.status_code}")
                batch = resp.json() or []
                for n in batch:
                    reason = str(n.get("reason") or "").strip()
                    full_name = str(((n.get("repository") or {}).get("full_name") or "")).strip()
                    if allowed_reasons and reason not in allowed_reasons:
                        continue
                    if not full_name:
                        continue
                    updated_at = _parse_dt(n.get("updated_at")) or poll_started_at
                    if updated_at > max_updated:
                        max_updated = updated_at
                    nid = str(n.get("id") or "")
                    if nid and nid > max_id:
                        max_id = nid
                    subject = n.get("subject") or {}
                    notifications.append({"notification_id": n.get("id"), "reason": reason, "updated_at": n.get("updated_at"), "repository_full_name": full_name, "subject_type": subject.get("type"), "subject_url": subject.get("url"), "latest_comment_url": subject.get("latest_comment_url"), "source_payload": n})
                if len(batch) < 100:
                    break
        return notifications, {"last_seen_notification_updated_at": _iso_z(max(max_updated, poll_started_at)), "last_seen_notification_id": max_id}

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
                resp = await client.get(f"{base_url}/orgs/{org}/repos", params={"per_page": 100, "page": page, "type": "all", "sort": "full_name", "direction": "asc"}, headers=headers)
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
        return sorted(output, key=lambda x: str(x.get("full_name") or ""))

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
        if surface == "discussion_comment":
            discussion = c.get("discussion") or {}
            comment = c.get("comment") or {}
            return {"source_kind": "github.mention", "source_event": "poll.discussion_comment", "comment_kind": "discussion_comment", "context_type": "discussion", "owner": owner, "repo": repo, "discussion_number": discussion.get("number"), "discussion_id": discussion.get("id"), "discussion_comment_id": comment.get("id"), "reply_to_id": ((comment.get("replyTo") or {}).get("id") if isinstance(comment.get("replyTo"), dict) else None), "comment_id": comment.get("id"), "body": comment.get("body"), "author": (comment.get("author") or {}).get("login"), "author_association": comment.get("authorAssociation"), "html_url": comment.get("url"), "created_at": comment.get("createdAt"), "updated_at": comment.get("updatedAt"), "mentioned_account": mention_target, "mentioned_logins": mentioned, "source_payload": {"discussion": discussion, "comment": comment}}
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
