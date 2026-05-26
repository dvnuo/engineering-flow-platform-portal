from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.services.provider_config_resolver import (
    GithubProviderConfig,
    JiraProviderConfig,
    resolve_github_for_agent,
    resolve_jira_for_agent,
)


DELEGATION_REPLY_MARKER_PREFIX = "<!-- efp:delegation-reply "
MAX_SOURCE_TEXT_CHARS = 20000
GITHUB_SOURCES = {"github_pr_review", "github_pr_mention"}
JIRA_SOURCES = {"jira_assignee", "jira_mention"}
SUPPORTED_DELEGATION_SOURCES = GITHUB_SOURCES | JIRA_SOURCES
SOURCE_PROVIDER = {
    "github_pr_review": "github",
    "github_pr_mention": "github",
    "jira_assignee": "jira",
    "jira_mention": "jira",
}


@dataclass
class SourcePollResult:
    items: list[dict[str, Any]]
    state_patch: dict[str, Any] = field(default_factory=dict)


class DelegationSourcePoller:
    async def poll(self, db: Session, rule) -> SourcePollResult:
        source = str(rule.trigger_type or "").strip()
        if source == "github_pr_review":
            return await self._poll_github_pr_review(db, rule)
        if source == "github_pr_mention":
            return await self._poll_github_pr_mention(db, rule)
        if source == "jira_assignee":
            return await self._poll_jira_assignee(db, rule)
        if source == "jira_mention":
            return await self._poll_jira_mention(db, rule)
        raise ValueError(f"Unsupported delegation source: {source}")

    @staticmethod
    def _github_headers(provider_config: GithubProviderConfig) -> dict:
        return {
            "Authorization": f"Bearer {provider_config.api_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _github_identity(self, client: httpx.AsyncClient, provider_config: GithubProviderConfig) -> str:
        response = await client.get(
            f"{provider_config.base_url.rstrip('/')}/user",
            headers=self._github_headers(provider_config),
        )
        response.raise_for_status()
        data = response.json()
        login = str(data.get("login") or "").strip()
        if not login:
            raise ValueError("GitHub /user response did not include login")
        return login

    @staticmethod
    def _github_repo_from_api_url(api_url: str) -> tuple[str, str, int] | None:
        parts = [p for p in str(api_url or "").split("/") if p]
        try:
            repos_idx = parts.index("repos")
            owner = parts[repos_idx + 1]
            repo = parts[repos_idx + 2]
            number = int(parts[-1])
            return owner, repo, number
        except Exception:
            return None

    @staticmethod
    def _bounded_text(value: Any, limit: int = MAX_SOURCE_TEXT_CHARS) -> str:
        text = str(value or "")
        if len(text) <= limit:
            return text
        return text[:limit] + "\n...[truncated]"

    @staticmethod
    def _github_user_login(user: Any) -> str | None:
        if not isinstance(user, dict):
            return None
        login = str(user.get("login") or "").strip()
        return login or None

    @classmethod
    def _github_pull_request_source_payload(
        cls,
        *,
        owner: str,
        repo: str,
        pull_number: int,
        pr_url: str,
        issue: dict,
        pull_payload: dict | None,
    ) -> dict[str, Any]:
        pull_payload = pull_payload if isinstance(pull_payload, dict) else {}
        issue = issue if isinstance(issue, dict) else {}
        pull_ref = issue.get("pull_request") if isinstance(issue.get("pull_request"), dict) else {}
        head = pull_payload.get("head") if isinstance(pull_payload.get("head"), dict) else {}
        base = pull_payload.get("base") if isinstance(pull_payload.get("base"), dict) else {}
        author = pull_payload.get("user") if isinstance(pull_payload.get("user"), dict) else issue.get("user")
        payload: dict[str, Any] = {
            "owner": owner,
            "repo": repo,
            "number": pull_number,
            "url": pr_url,
        }
        api_url = str(pull_payload.get("url") or pull_ref.get("url") or "").strip()
        if api_url:
            payload["api_url"] = api_url
        title = str(pull_payload.get("title") or issue.get("title") or "").strip()
        if title:
            payload["title"] = cls._bounded_text(title, limit=1000)
        head_sha = str(head.get("sha") or "").strip()
        if head_sha:
            payload["head_sha"] = head_sha
        base_sha = str(base.get("sha") or "").strip()
        if base_sha:
            payload["base_sha"] = base_sha
        author_login = cls._github_user_login(author)
        if author_login:
            payload["author"] = author_login
        for key in ("state", "created_at", "updated_at"):
            value = str(pull_payload.get(key) or issue.get(key) or "").strip()
            if value:
                payload[key] = value
        if "draft" in pull_payload:
            payload["draft"] = bool(pull_payload.get("draft"))
        return payload

    @classmethod
    def _github_comment_source_payload(cls, comment: dict, comment_kind: str) -> dict[str, Any]:
        author_login = cls._github_user_login(comment.get("user") if isinstance(comment, dict) else None)
        payload: dict[str, Any] = {
            "kind": comment_kind,
            "id": comment.get("id") if isinstance(comment, dict) else None,
            "body": cls._bounded_text(comment.get("body") if isinstance(comment, dict) else ""),
        }
        for key in ("html_url", "created_at", "updated_at"):
            value = str(comment.get(key) or "").strip() if isinstance(comment, dict) else ""
            if value:
                payload[key] = value
        if author_login:
            payload["author"] = author_login
        for key in ("path", "commit_id", "pull_request_review_id"):
            value = comment.get(key) if isinstance(comment, dict) else None
            if value not in (None, ""):
                payload[key] = value
        return payload

    @staticmethod
    def _github_pr_reaction_target(*, owner: str, repo: str, pull_number: int, html_url: str) -> dict[str, Any]:
        target: dict[str, Any] = {
            "provider": "github",
            "kind": "pull_request",
            "owner": owner,
            "repo": repo,
            "pull_number": pull_number,
            "api_path": f"/repos/{owner}/{repo}/issues/{pull_number}/reactions",
        }
        if html_url:
            target["html_url"] = html_url
        return target

    @staticmethod
    def _github_comment_reaction_target(
        *,
        owner: str,
        repo: str,
        pull_number: int,
        comment_id: Any,
        comment_kind: str,
        html_url: str | None = None,
    ) -> dict[str, Any]:
        normalized_kind = "pull_request_review_comment" if comment_kind == "pull_request_review_comment" else "issue_comment"
        if normalized_kind == "pull_request_review_comment":
            api_path = f"/repos/{owner}/{repo}/pulls/comments/{comment_id}/reactions"
        else:
            api_path = f"/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions"
        target: dict[str, Any] = {
            "provider": "github",
            "kind": normalized_kind,
            "owner": owner,
            "repo": repo,
            "pull_number": pull_number,
            "comment_id": comment_id,
            "api_path": api_path,
        }
        if html_url:
            target["html_url"] = html_url
        return target

    @staticmethod
    def _github_pr_directly_requests_login(pull_payload: dict, login: str) -> bool:
        requested_reviewers = pull_payload.get("requested_reviewers")
        if not isinstance(requested_reviewers, list):
            return False
        normalized_login = str(login or "").strip().lower()
        if not normalized_login:
            return False
        for reviewer in requested_reviewers:
            if not isinstance(reviewer, dict):
                continue
            reviewer_login = str(reviewer.get("login") or "").strip().lower()
            if reviewer_login == normalized_login:
                return True
        return False

    async def _poll_github_pr_review(self, db: Session, rule) -> SourcePollResult:
        provider_config = resolve_github_for_agent(db, rule.target_agent_id)
        base_url = provider_config.base_url.rstrip("/")
        headers = self._github_headers(provider_config)
        async with httpx.AsyncClient(timeout=30.0) as client:
            login = await self._github_identity(client, provider_config)
            query = f"is:pr is:open review-requested:{login}"
            search_response = await client.get(
                f"{base_url}/search/issues",
                headers=headers,
                params={"q": query, "sort": "updated", "order": "desc", "per_page": 50},
            )
            search_response.raise_for_status()
            search_items = search_response.json().get("items") or []
            items: list[dict[str, Any]] = []
            for issue in search_items:
                pull = issue.get("pull_request") if isinstance(issue, dict) else None
                pull_url = (pull or {}).get("url") if isinstance(pull, dict) else None
                repo_tuple = self._github_repo_from_api_url(pull_url or issue.get("url"))
                if not pull_url or not repo_tuple:
                    continue
                pull_response = await client.get(pull_url, headers=headers)
                pull_response.raise_for_status()
                pull_payload = pull_response.json()
                if not self._github_pr_directly_requests_login(pull_payload, login):
                    continue
                owner, repo, pull_number = repo_tuple
                head_sha = str(((pull_payload.get("head") or {}).get("sha")) or "").strip()
                pr_url = str(pull_payload.get("html_url") or issue.get("html_url") or "").strip()
                if not head_sha or not pr_url:
                    continue
                dedupe_key = f"github_pr_review:{owner}/{repo}:{pull_number}:{head_sha}"
                source_payload = {
                    "pull_request": self._github_pull_request_source_payload(
                        owner=owner,
                        repo=repo,
                        pull_number=pull_number,
                        pr_url=pr_url,
                        issue=issue,
                        pull_payload=pull_payload,
                    )
                }
                items.append(
                    {
                        "source": "github_pr_review",
                        "provider": "github",
                        "dedupe_key": dedupe_key,
                        "version_key": head_sha,
                        "source_url": pr_url,
                        "task_content": f"Review this GitHub PR:\n{pr_url}",
                        "represented_identity": login,
                        "source_payload": source_payload,
                        "reply_target": {
                            "provider": "github",
                            "kind": "pr_comment",
                            "owner": owner,
                            "repo": repo,
                            "pull_number": pull_number,
                        },
                        "reaction_target": self._github_pr_reaction_target(
                            owner=owner,
                            repo=repo,
                            pull_number=pull_number,
                            html_url=pr_url,
                        ),
                    }
                )
        return SourcePollResult(items=items)

    @staticmethod
    def _mentions_login(body: str, login: str) -> bool:
        return f"@{login.lower()}" in str(body or "").lower()

    async def _github_issue_comments(self, client: httpx.AsyncClient, base_url: str, headers: dict, owner: str, repo: str, number: int) -> list[dict]:
        response = await client.get(
            f"{base_url}/repos/{owner}/{repo}/issues/{number}/comments",
            headers=headers,
            params={"per_page": 50},
        )
        response.raise_for_status()
        comments = response.json()
        return comments if isinstance(comments, list) else []

    async def _github_review_comments(self, client: httpx.AsyncClient, base_url: str, headers: dict, owner: str, repo: str, number: int) -> list[dict]:
        response = await client.get(
            f"{base_url}/repos/{owner}/{repo}/pulls/{number}/comments",
            headers=headers,
            params={"per_page": 50},
        )
        response.raise_for_status()
        comments = response.json()
        return comments if isinstance(comments, list) else []

    async def _poll_github_pr_mention(self, db: Session, rule) -> SourcePollResult:
        provider_config = resolve_github_for_agent(db, rule.target_agent_id)
        base_url = provider_config.base_url.rstrip("/")
        headers = self._github_headers(provider_config)
        async with httpx.AsyncClient(timeout=30.0) as client:
            login = await self._github_identity(client, provider_config)
            query = f"is:pr is:open mentions:{login}"
            search_response = await client.get(
                f"{base_url}/search/issues",
                headers=headers,
                params={"q": query, "sort": "updated", "order": "desc", "per_page": 30},
            )
            search_response.raise_for_status()
            search_items = search_response.json().get("items") or []
            items: list[dict[str, Any]] = []
            for issue in search_items:
                repo_tuple = self._github_repo_from_api_url(issue.get("url") or "")
                if not repo_tuple:
                    continue
                owner, repo, pull_number = repo_tuple
                pr_url = str(issue.get("html_url") or "").strip()
                pull_ref = issue.get("pull_request") if isinstance(issue.get("pull_request"), dict) else {}
                pull_payload: dict[str, Any] = {}
                pull_url = str(pull_ref.get("url") or "").strip()
                if pull_url:
                    pull_response = await client.get(pull_url, headers=headers)
                    pull_response.raise_for_status()
                    pulled = pull_response.json()
                    pull_payload = pulled if isinstance(pulled, dict) else {}
                pull_context = self._github_pull_request_source_payload(
                    owner=owner,
                    repo=repo,
                    pull_number=pull_number,
                    pr_url=pr_url,
                    issue=issue,
                    pull_payload=pull_payload,
                )
                comment_batches = (
                    ("issue_comment", await self._github_issue_comments(client, base_url, headers, owner, repo, pull_number)),
                    (
                        "pull_request_review_comment",
                        await self._github_review_comments(client, base_url, headers, owner, repo, pull_number),
                    ),
                )
                for comment_kind, comments in comment_batches:
                    for comment in comments:
                        body = str(comment.get("body") or "")
                        if not body or DELEGATION_REPLY_MARKER_PREFIX in body or not self._mentions_login(body, login):
                            continue
                        comment_id = str(comment.get("id") or "").strip()
                        if not comment_id:
                            continue
                        dedupe_key = f"github_pr_mention:{owner}/{repo}:{pull_number}:comment:{comment_id}"
                        items.append(
                            {
                                "source": "github_pr_mention",
                                "provider": "github",
                                "dedupe_key": dedupe_key,
                                "version_key": comment_id,
                                "source_url": pr_url,
                                "source_comment": body,
                                "task_content": f"You are responding as @{login}.\nGitHub PR:\n{pr_url}\n\nComment:\n{body}",
                                "represented_identity": f"@{login}",
                                "source_payload": {
                                    "pull_request": pull_context,
                                    "comment": self._github_comment_source_payload(comment, comment_kind),
                                },
                                "reply_target": {
                                    "provider": "github",
                                    "kind": "pr_comment",
                                    "owner": owner,
                                    "repo": repo,
                                    "pull_number": pull_number,
                                },
                                "reaction_target": self._github_comment_reaction_target(
                                    owner=owner,
                                    repo=repo,
                                    pull_number=pull_number,
                                    comment_id=comment.get("id"),
                                    comment_kind=comment_kind,
                                    html_url=str(comment.get("html_url") or "").strip() or None,
                                ),
                            }
                        )
        return SourcePollResult(items=items)

    async def _jira_identity(self, client: httpx.AsyncClient, provider_config: JiraProviderConfig) -> dict[str, Any]:
        response = await client.get(
            f"{provider_config.base_url}/rest/api/{provider_config.api_version}/myself",
            headers=provider_config.headers,
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _jira_issue_url(provider_config: JiraProviderConfig, issue_key: str) -> str:
        return f"{provider_config.base_url}/browse/{issue_key}"

    @staticmethod
    def _jira_user_source_payload(user: Any) -> dict[str, Any]:
        if not isinstance(user, dict):
            return {}
        payload: dict[str, Any] = {}
        for key in ("accountId", "displayName", "key", "name", "emailAddress"):
            value = str(user.get(key) or "").strip()
            if value:
                payload[key] = value
        return payload

    @classmethod
    def _jira_issue_source_payload(cls, provider_config: JiraProviderConfig, issue: dict) -> dict[str, Any]:
        key = str(issue.get("key") or "").strip()
        fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
        status = fields.get("status") if isinstance(fields.get("status"), dict) else {}
        status_payload: dict[str, Any] = {}
        for status_key in ("id", "name"):
            value = str(status.get(status_key) or "").strip()
            if value:
                status_payload[status_key] = value
        category = status.get("statusCategory") if isinstance(status.get("statusCategory"), dict) else {}
        category_name = str(category.get("name") or "").strip()
        if category_name:
            status_payload["category"] = category_name
        payload: dict[str, Any] = {
            "key": key,
            "url": cls._jira_issue_url(provider_config, key) if key else "",
        }
        summary = str(fields.get("summary") or "").strip()
        if summary:
            payload["summary"] = cls._bounded_text(summary, limit=1000)
        if status_payload:
            payload["status"] = status_payload
        updated = str(fields.get("updated") or "").strip()
        if updated:
            payload["updated"] = updated
        reporter = cls._jira_user_source_payload(fields.get("reporter"))
        if reporter:
            payload["reporter"] = reporter
        assignee = cls._jira_user_source_payload(fields.get("assignee"))
        if assignee:
            payload["assignee"] = assignee
        return payload

    @classmethod
    def _jira_comment_source_payload(cls, comment: dict) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": str(comment.get("id") or "").strip(),
            "body": cls._bounded_text(cls._jira_comment_text(comment.get("body"))),
        }
        author = cls._jira_user_source_payload(comment.get("author"))
        if author:
            payload["author"] = author
        for key in ("created", "updated"):
            value = str(comment.get(key) or "").strip()
            if value:
                payload[key] = value
        return payload

    async def _jira_search(self, client: httpx.AsyncClient, provider_config: JiraProviderConfig, jql: str) -> list[dict]:
        response = await client.get(
            f"{provider_config.base_url}/rest/api/{provider_config.api_version}/search",
            headers=provider_config.headers,
            params={"jql": jql, "maxResults": 50, "fields": "summary,status,reporter,assignee,updated,comment"},
        )
        response.raise_for_status()
        issues = response.json().get("issues") or []
        return issues if isinstance(issues, list) else []

    async def _poll_jira_assignee(self, db: Session, rule) -> SourcePollResult:
        provider_config = resolve_jira_for_agent(db, rule.target_agent_id)
        async with httpx.AsyncClient(timeout=30.0) as client:
            identity = await self._jira_identity(client, provider_config)
            represented = str(identity.get("displayName") or identity.get("emailAddress") or identity.get("accountId") or "").strip()
            issues = await self._jira_search(client, provider_config, "assignee = currentUser() ORDER BY updated DESC")
            items: list[dict[str, Any]] = []
            for issue in issues:
                key = str(issue.get("key") or "").strip()
                if not key:
                    continue
                fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
                updated = str(fields.get("updated") or "").strip()
                issue_url = self._jira_issue_url(provider_config, key)
                items.append(
                    {
                        "source": "jira_assignee",
                        "provider": "jira",
                        "dedupe_key": f"jira_assignee:{key}:{updated or 'unknown'}",
                        "version_key": updated or key,
                        "source_url": issue_url,
                        "task_content": f"Work on this Jira issue:\n{issue_url}",
                        "represented_identity": represented,
                        "source_payload": {"issue": self._jira_issue_source_payload(provider_config, issue)},
                        "reply_target": {"provider": "jira", "kind": "issue_comment", "issue_key": key},
                    }
                )
        return SourcePollResult(items=items)

    @staticmethod
    def _jira_comment_text(body: Any) -> str:
        if isinstance(body, str):
            return body
        if body is None:
            return ""
        return json.dumps(body, ensure_ascii=False)

    @staticmethod
    def _jira_mention_tokens(identity: dict[str, Any]) -> list[str]:
        tokens = []
        for key in ("displayName", "emailAddress", "accountId"):
            value = str(identity.get(key) or "").strip()
            if value:
                tokens.append(value)
        account_id = str(identity.get("accountId") or "").strip()
        if account_id:
            tokens.append(f"[~accountid:{account_id}]")
        return tokens

    @staticmethod
    def _jira_jql_text_literal(value: str) -> str:
        return str(value or "").strip().replace("\\", "\\\\").replace('"', '\\"')

    async def _poll_jira_mention(self, db: Session, rule) -> SourcePollResult:
        provider_config = resolve_jira_for_agent(db, rule.target_agent_id)
        async with httpx.AsyncClient(timeout=30.0) as client:
            identity = await self._jira_identity(client, provider_config)
            tokens = self._jira_mention_tokens(identity)
            represented = str(identity.get("displayName") or identity.get("emailAddress") or identity.get("accountId") or "").strip()
            if not tokens:
                raise ValueError("Jira /myself response did not include a usable identity")
            jql_literals = [self._jira_jql_text_literal(token) for token in tokens[:2]]
            jql_terms = " OR ".join(f'text ~ "{literal}"' for literal in jql_literals if literal)
            issues = await self._jira_search(client, provider_config, f"({jql_terms}) ORDER BY updated DESC")
            items: list[dict[str, Any]] = []
            for issue in issues:
                key = str(issue.get("key") or "").strip()
                if not key:
                    continue
                issue_url = self._jira_issue_url(provider_config, key)
                fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
                comments_block = fields.get("comment") if isinstance(fields.get("comment"), dict) else {}
                comments = comments_block.get("comments") if isinstance(comments_block, dict) else []
                for comment in comments if isinstance(comments, list) else []:
                    body = self._jira_comment_text(comment.get("body"))
                    if DELEGATION_REPLY_MARKER_PREFIX in body:
                        continue
                    lowered = body.lower()
                    if not any(token.lower() in lowered for token in tokens):
                        continue
                    comment_id = str(comment.get("id") or "").strip()
                    if not comment_id:
                        continue
                    updated = str(comment.get("updated") or comment.get("created") or "").strip()
                    items.append(
                        {
                            "source": "jira_mention",
                            "provider": "jira",
                            "dedupe_key": f"jira_mention:{key}:comment:{comment_id}",
                            "version_key": updated or comment_id,
                            "source_url": issue_url,
                            "source_comment": body,
                            "task_content": f"You are responding as {represented}.\nJira issue:\n{issue_url}\n\nComment:\n{body}",
                            "represented_identity": represented,
                            "source_payload": {
                                "issue": self._jira_issue_source_payload(provider_config, issue),
                                "comment": self._jira_comment_source_payload(comment),
                            },
                            "reply_target": {"provider": "jira", "kind": "issue_comment", "issue_key": key},
                        }
                    )
        return SourcePollResult(items=items)
