from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.services.provider_config_resolver import (
    GithubProviderConfig,
    JiraProviderConfig,
    resolve_github_for_agent,
    resolve_jira_for_agent,
)


AUTO_REPLY_MARKER_PREFIX = "<!-- efp:auto-reply "
GITHUB_SOURCES = {"github_pr_review", "github_pr_mention"}
JIRA_SOURCES = {"jira_assignee", "jira_mention"}
SUPPORTED_AUTOMATION_SOURCES = GITHUB_SOURCES | JIRA_SOURCES
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


class AutomationSourcePoller:
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
        raise ValueError(f"Unsupported automation source: {source}")

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
                owner, repo, pull_number = repo_tuple
                head_sha = str(((pull_payload.get("head") or {}).get("sha")) or "").strip()
                pr_url = str(pull_payload.get("html_url") or issue.get("html_url") or "").strip()
                if not head_sha or not pr_url:
                    continue
                dedupe_key = f"github_pr_review:{owner}/{repo}:{pull_number}:{head_sha}"
                items.append(
                    {
                        "source": "github_pr_review",
                        "provider": "github",
                        "dedupe_key": dedupe_key,
                        "version_key": head_sha,
                        "source_url": pr_url,
                        "task_content": f"Review this GitHub PR:\n{pr_url}",
                        "represented_identity": login,
                        "source_payload": {"issue": issue, "pull_request": pull_payload},
                        "reply_target": {
                            "provider": "github",
                            "kind": "pr_comment",
                            "owner": owner,
                            "repo": repo,
                            "pull_number": pull_number,
                        },
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
                comments = await self._github_issue_comments(client, base_url, headers, owner, repo, pull_number)
                comments.extend(await self._github_review_comments(client, base_url, headers, owner, repo, pull_number))
                for comment in comments:
                    body = str(comment.get("body") or "")
                    if not body or AUTO_REPLY_MARKER_PREFIX in body or not self._mentions_login(body, login):
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
                            "source_payload": {"issue": issue, "comment": comment},
                            "reply_target": {
                                "provider": "github",
                                "kind": "pr_comment",
                                "owner": owner,
                                "repo": repo,
                                "pull_number": pull_number,
                            },
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

    async def _jira_search(self, client: httpx.AsyncClient, provider_config: JiraProviderConfig, jql: str) -> list[dict]:
        response = await client.get(
            f"{provider_config.base_url}/rest/api/{provider_config.api_version}/search",
            headers=provider_config.headers,
            params={"jql": jql, "maxResults": 50, "fields": "summary,updated,comment"},
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
                        "source_payload": {"issue": issue},
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

    async def _poll_jira_mention(self, db: Session, rule) -> SourcePollResult:
        provider_config = resolve_jira_for_agent(db, rule.target_agent_id)
        async with httpx.AsyncClient(timeout=30.0) as client:
            identity = await self._jira_identity(client, provider_config)
            tokens = self._jira_mention_tokens(identity)
            represented = str(identity.get("displayName") or identity.get("emailAddress") or identity.get("accountId") or "").strip()
            if not tokens:
                raise ValueError("Jira /myself response did not include a usable identity")
            jql_terms = " OR ".join(f'text ~ "{quote(token)}"' for token in tokens[:2])
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
                    if AUTO_REPLY_MARKER_PREFIX in body:
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
                            "source_payload": {"issue": issue, "comment": comment},
                            "reply_target": {"provider": "jira", "kind": "issue_comment", "issue_key": key},
                        }
                    )
        return SourcePollResult(items=items)
