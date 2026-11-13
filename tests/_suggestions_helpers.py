"""Shared scaffolding for the picker-source tests.

Every source is exercised against an ``httpx.MockTransport``: the test sees
exactly which requests the source makes (the JQL, the search qualifiers, the
GraphQL variables) and nothing leaves the process.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import httpx

from app.services.suggestions import suggest
from app.services.suggestions.base import ConnectorHttp, Result, SourceContext

OWN = {"github": "custom", "jira": "custom", "confluence": "custom", "jenkins": "custom", "aws": "custom"}
SHARED: dict[str, str] = {}

JIRA_CONFIG = {"jira": {"enabled": True, "instances": [{"name": "Main", "url": "https://jira.corp", "token": "pat-1"}]}}
GITHUB_CONFIG = {"github": {"enabled": True, "api_token": "ghp-1"}}
CONFLUENCE_CONFIG = {
    "confluence": {"enabled": True, "instances": [{"name": "Wiki", "url": "https://wiki.corp", "username": "alice", "password": "pw"}]}
}


class Recorder:
    """A mock transport that answers by ``(method, path)`` and keeps what was asked."""

    def __init__(self, routes: dict[tuple[str, str], Any] | None = None, fallback: Callable | None = None) -> None:
        self.routes = dict(routes or {})
        self.fallback = fallback
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.routes.get((request.method, request.url.path))
        if answer is None and self.fallback is not None:
            answer = self.fallback(request)
        if answer is None:
            return httpx.Response(404, json={"message": "not found"})
        if callable(answer):
            answer = answer(request)
        if isinstance(answer, httpx.Response):
            return answer
        if isinstance(answer, BaseException):
            raise answer
        return httpx.Response(200, json=answer)

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    def queries(self, path: str, name: str) -> list[str]:
        """Every value of one query parameter sent to one path, in order."""
        values: list[str] = []
        for request in self.requests:
            if request.url.path == path:
                values += parse_qs(urlparse(str(request.url)).query).get(name, [])
        return values

    def bodies(self, path: str) -> list[dict]:
        return [json.loads(request.content) for request in self.requests if request.url.path == path and request.content]


def make_context(
    recorder: Recorder,
    config: dict,
    *,
    username: str = "alice",
    modes: dict[str, str] | None = None,
    q: str = "",
    params: dict[str, list[str]] | None = None,
    recent: list[str] | None = None,
    suffix: str = "",
    runtime_get=None,
    agent=None,
    db=None,
    revision: int = 1,
    user_id: int = 1,
    role: str = "user",
) -> SourceContext:
    return SourceContext(
        user=SimpleNamespace(id=user_id, username=username, role=role),
        config=config,
        http=ConnectorHttp(config, transport=httpx.MockTransport(recorder)),
        db=db,
        settings=SimpleNamespace(github_username_suffix=suffix),
        modes=dict(OWN if modes is None else modes),
        revision=revision,
        q=q,
        params=dict(params or {}),
        recent=list(recent or []),
        agent=agent,
        runtime_get=runtime_get,
    )


def run(source_id: str, context: SourceContext) -> Result:
    return asyncio.run(suggest(source_id, context))


def group_ids(result: Result) -> list[str]:
    return [group.id for group in result.groups if group.items]


def values(result: Result, group_id: str) -> list[str]:
    for group in result.groups:
        if group.id == group_id:
            return [item.value for item in group.items]
    return []
