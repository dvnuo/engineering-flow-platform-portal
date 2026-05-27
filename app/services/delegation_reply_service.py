from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.services.provider_config_resolver import resolve_github_for_agent, resolve_jira_for_agent


DELEGATION_REPLY_MARKER_PREFIX = "<!-- efp:delegation-reply "
GITHUB_QUOTE_REPLY_MAX_CHARS = 4000


def delegation_reply_marker(rule_id: str, event_id: str) -> str:
    return f"<!-- efp:delegation-reply delegation_id={rule_id} event_id={event_id} -->"


class DelegationReplyService:
    async def send_reply(self, db: Session, *, rule, event, reply_target: dict, text: str) -> None:
        provider = str((reply_target or {}).get("provider") or rule.source_type or "").strip().lower()
        if provider == "github":
            if str((reply_target or {}).get("reply_mode") or "").strip() == "quote_reply":
                text = self.format_github_quote_reply_body(reply_target=reply_target, text=text)
            await self._send_github_reply(db, rule=rule, reply_target=reply_target, text=text)
            return
        if provider == "jira":
            await self._send_jira_reply(db, rule=rule, reply_target=reply_target, text=text)
            return
        raise ValueError(f"Unsupported reply provider: {provider or 'empty'}")

    @staticmethod
    def _github_headers(provider_config) -> dict:
        return {
            "Authorization": f"Bearer {provider_config.api_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    @staticmethod
    def _github_api_url(base_url: str, api_path: str) -> str:
        normalized_path = str(api_path or "").strip()
        if not normalized_path:
            raise ValueError("GitHub API path is missing")
        if not normalized_path.startswith("/"):
            normalized_path = "/" + normalized_path
        return f"{base_url.rstrip('/')}{normalized_path}"

    @staticmethod
    def _github_error_message(method: str, api_path: str, response: httpx.Response) -> str:
        body = (response.text or "").strip()
        if len(body) > 1000:
            body = body[:1000] + "...[truncated]"
        suffix = f": {body}" if body else ""
        return f"GitHub {method} {api_path} failed with status {response.status_code}{suffix}"

    @staticmethod
    def _github_reaction_target_debug(reaction_target: dict) -> dict[str, Any]:
        keys = (
            "provider",
            "kind",
            "owner",
            "repo",
            "pull_number",
            "comment_id",
            "html_url",
            "api_path",
        )
        return {key: reaction_target.get(key) for key in keys if reaction_target.get(key) not in (None, "")}

    async def add_github_reaction(self, db: Session, *, rule, reaction_target: dict, content: str = "eyes") -> dict[str, Any]:
        target = reaction_target if isinstance(reaction_target, dict) else {}
        provider = str(target.get("provider") or "github").strip().lower()
        if provider != "github":
            raise ValueError(f"Unsupported reaction provider: {provider or 'empty'}")
        api_path = str(target.get("api_path") or "").strip()
        provider_config = resolve_github_for_agent(db, rule.target_agent_id)
        headers = {**self._github_headers(provider_config), "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self._github_api_url(provider_config.base_url, api_path),
                headers=headers,
                json={"content": content},
            )
        if not (200 <= response.status_code < 300):
            raise RuntimeError(self._github_error_message("POST", api_path, response))
        try:
            payload = response.json()
        except Exception:
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        reaction_id = payload.get("id")
        cleanup_api_path = f"{api_path.rstrip('/')}/{reaction_id}" if reaction_id not in (None, "") else None
        metadata: dict[str, Any] = {
            "provider": "github",
            "content": str(payload.get("content") or content),
            "api_path": api_path,
            "target": self._github_reaction_target_debug(target),
        }
        if reaction_id not in (None, ""):
            metadata["reaction_id"] = reaction_id
        if cleanup_api_path:
            metadata["cleanup_api_path"] = cleanup_api_path
        reaction_url = str(payload.get("url") or "").strip()
        if reaction_url:
            metadata["reaction_url"] = reaction_url
        return metadata

    async def delete_github_reaction(
        self,
        db: Session,
        *,
        rule,
        cleanup_api_path: str | None = None,
        portal_start_reaction: dict | None = None,
    ) -> dict[str, Any]:
        reaction = portal_start_reaction if isinstance(portal_start_reaction, dict) else {}
        path = str(cleanup_api_path or reaction.get("cleanup_api_path") or "").strip()
        if not path:
            api_path = str(reaction.get("api_path") or "").strip()
            reaction_id = reaction.get("reaction_id")
            if api_path and reaction_id not in (None, ""):
                path = f"{api_path.rstrip('/')}/{reaction_id}"
        if not path:
            raise ValueError("GitHub reaction cleanup path is missing")

        provider_config = resolve_github_for_agent(db, rule.target_agent_id)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(
                self._github_api_url(provider_config.base_url, path),
                headers=self._github_headers(provider_config),
            )
        if response.status_code == 404:
            return {"provider": "github", "status": "not_found", "cleanup_api_path": path}
        if not (200 <= response.status_code < 300):
            raise RuntimeError(self._github_error_message("DELETE", path, response))
        return {"provider": "github", "status": "deleted", "cleanup_api_path": path}

    @staticmethod
    def _jira_comment_api_path(api_version: str, issue_key: str, comment_id: str | None = None) -> str:
        path = f"/rest/api/{api_version}/issue/{issue_key}/comment"
        if comment_id not in (None, ""):
            path = f"{path}/{comment_id}"
        return path

    @staticmethod
    def _format_jira_start_comment_body(
        *,
        issue_key: str,
        source: str,
        source_url: str | None = None,
        marker: str | None = None,
    ) -> str:
        source_label = str(source or "").strip() or "delegation"
        lines: list[str] = []
        marker_line = str(marker or "").strip()
        if marker_line:
            lines.extend([marker_line, ""])
        lines.extend(
            [
                "Automated EFP delegation run has started.",
                "",
                f"Source: {source_label}",
                f"Issue: {issue_key}",
            ]
        )
        url = str(source_url or "").strip()
        if url:
            lines.append(f"Link: {url}")
        return "\n".join(lines)

    async def add_jira_start_comment(
        self,
        db: Session,
        rule,
        reply_target: dict,
        *,
        source: str,
        source_url: str | None = None,
        source_comment: str | None = None,
        event=None,
        marker: str | None = None,
    ) -> dict[str, Any]:
        target = reply_target if isinstance(reply_target, dict) else {}
        provider = str(target.get("provider") or "jira").strip().lower()
        if provider != "jira":
            raise ValueError(f"Unsupported Jira start comment provider: {provider or 'empty'}")
        kind = str(target.get("kind") or "").strip()
        if kind != "issue_comment":
            raise ValueError(f"Unsupported Jira start comment target: {kind or 'empty'}")
        issue_key = str(target.get("issue_key") or "").strip()
        if not issue_key:
            raise ValueError("Jira start comment target is missing issue_key")

        provider_config = resolve_jira_for_agent(db, rule.target_agent_id)
        api_path = self._jira_comment_api_path(provider_config.api_version, issue_key)
        marker_line = str(marker or "").strip()
        if not marker_line and event is not None:
            marker_line = delegation_reply_marker(rule.id, event.id)
        # Do not echo source_comment here; Jira mention text can retrigger polling.
        _ = source_comment
        content = self._format_jira_start_comment_body(
            issue_key=issue_key,
            source=source,
            source_url=source_url,
            marker=marker_line,
        )
        base_url = provider_config.base_url.rstrip("/")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{base_url}{api_path}",
                headers={**provider_config.headers, "Accept": "application/json", "Content-Type": "application/json"},
                json={"body": content},
            )
            response.raise_for_status()

        try:
            payload = response.json()
        except Exception:
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        comment_id = str(payload.get("id") or "").strip()
        if not comment_id:
            raise RuntimeError(f"Jira start comment response for {issue_key} did not include comment id")

        metadata: dict[str, Any] = {
            "provider": "jira",
            "status": "created",
            "issue_key": issue_key,
            "comment_id": comment_id,
            "api_path": api_path,
            "content": content,
        }
        comment_url = str(payload.get("self") or "").strip()
        if comment_url:
            metadata["comment_url"] = comment_url
        return metadata

    @staticmethod
    def _split_marker_prefixed_text(text: str) -> tuple[str | None, str]:
        normalized = str(text or "")
        lines = normalized.splitlines()
        if lines and lines[0].strip().startswith(DELEGATION_REPLY_MARKER_PREFIX):
            return lines[0].strip(), "\n".join(lines[1:]).lstrip("\n")
        return None, normalized

    @staticmethod
    def _quote_markdown_body(body: str, *, max_quote_chars: int = GITHUB_QUOTE_REPLY_MAX_CHARS) -> str:
        quote = str(body or "")
        if len(quote) > max_quote_chars:
            quote = quote[:max_quote_chars].rstrip() + "\n[truncated]"
        if quote == "":
            return "> "
        return "\n".join(f"> {line}" for line in quote.splitlines())

    @classmethod
    def format_github_quote_reply_body(
        cls,
        *,
        reply_target: dict,
        text: str,
        max_quote_chars: int = GITHUB_QUOTE_REPLY_MAX_CHARS,
    ) -> str:
        target = reply_target if isinstance(reply_target, dict) else {}
        marker, response_body = cls._split_marker_prefixed_text(text)
        author = str(target.get("comment_author") or "").strip()
        comment_url = str(target.get("comment_html_url") or "").strip()
        if author and comment_url:
            author_ref = author if author.startswith("@") else f"@{author}"
            header = f"Replying to {author_ref}'s [comment]({comment_url}):"
        else:
            header = "Replying to the triggering comment:"
        quoted = cls._quote_markdown_body(str(target.get("comment_body") or ""), max_quote_chars=max_quote_chars)
        parts = [header, quoted]
        if response_body.strip():
            parts.append(response_body.strip())
        body = "\n\n".join(parts)
        if marker:
            return f"{marker}\n\n{body}"
        return body

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
        headers = self._github_headers(provider_config)
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
        base_api_path = self._jira_comment_api_path(provider_config.api_version, issue_key)
        comment_id = str(reply_target.get("comment_id") or "").strip()
        should_update = str(reply_target.get("reply_mode") or "").strip() == "update_comment" and bool(comment_id)
        api_path = self._jira_comment_api_path(provider_config.api_version, issue_key, comment_id) if should_update else base_api_path
        base_url = provider_config.base_url.rstrip("/")
        async with httpx.AsyncClient(timeout=30.0) as client:
            request_kwargs = {
                "headers": {**provider_config.headers, "Accept": "application/json", "Content-Type": "application/json"},
                "json": {"body": text},
            }
            if should_update:
                response = await client.put(f"{base_url}{api_path}", **request_kwargs)
            else:
                response = await client.post(f"{base_url}{api_path}", **request_kwargs)
            response.raise_for_status()
