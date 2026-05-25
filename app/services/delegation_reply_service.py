from __future__ import annotations

import httpx
from sqlalchemy.orm import Session

from app.services.provider_config_resolver import resolve_github_for_agent, resolve_jira_for_agent


def delegation_reply_marker(rule_id: str, event_id: str) -> str:
    return f"<!-- efp:delegation-reply delegation_id={rule_id} event_id={event_id} -->"


class DelegationReplyService:
    async def send_reply(self, db: Session, *, rule, event, reply_target: dict, text: str) -> None:
        provider = str((reply_target or {}).get("provider") or rule.source_type or "").strip().lower()
        if provider == "github":
            await self._send_github_reply(db, rule=rule, reply_target=reply_target, text=text)
            return
        if provider == "jira":
            await self._send_jira_reply(db, rule=rule, reply_target=reply_target, text=text)
            return
        raise ValueError(f"Unsupported reply provider: {provider or 'empty'}")

    async def _send_github_reply(self, db: Session, *, rule, reply_target: dict, text: str) -> None:
        kind = str((reply_target or {}).get("kind") or "").strip()
        if kind != "pr_comment":
            raise ValueError(f"Unsupported GitHub reply target: {kind or 'empty'}")
        owner = str(reply_target.get("owner") or "").strip()
        repo = str(reply_target.get("repo") or "").strip()
        pull_number = reply_target.get("pull_number")
        if not owner or not repo or pull_number in (None, ""):
            raise ValueError("GitHub reply target is missing owner, repo, or pull_number")
        provider_config = resolve_github_for_agent(db, rule.target_agent_id)
        base_url = provider_config.base_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {provider_config.api_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{base_url}/repos/{owner}/{repo}/issues/{pull_number}/comments",
                headers=headers,
                json={"body": text},
            )
            response.raise_for_status()

    async def _send_jira_reply(self, db: Session, *, rule, reply_target: dict, text: str) -> None:
        kind = str((reply_target or {}).get("kind") or "").strip()
        if kind != "issue_comment":
            raise ValueError(f"Unsupported Jira reply target: {kind or 'empty'}")
        issue_key = str(reply_target.get("issue_key") or "").strip()
        if not issue_key:
            raise ValueError("Jira reply target is missing issue_key")
        provider_config = resolve_jira_for_agent(db, rule.target_agent_id)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{provider_config.base_url}/rest/api/{provider_config.api_version}/issue/{issue_key}/comment",
                headers={**provider_config.headers, "Accept": "application/json", "Content-Type": "application/json"},
                json={"body": text},
            )
            response.raise_for_status()
