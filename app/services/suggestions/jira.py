"""Jira: the issues and projects a member is working with.

Read with the member's Jira connector, against the first instance that has a
complete credential (the one the Test button probes). Server and Data Center
(/rest/api/2) are what the platform's CLI supports; an instance marked as API
version 3 (Cloud) is read through the search endpoint Cloud kept.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.services import connector_http
from app.services.suggestions.base import (
    Group,
    Item,
    Result,
    SourceContext,
    SourceNotConfigured,
    SourceUnavailable,
    gather_groups,
    json_of,
    search_words,
)
from app.services.suggestions.cache import TTLCache
from app.services.suggestions.identity import Identity, atlassian_identity

ISSUE_FIELDS = "summary,status,issuetype,project,updated"
GROUP_LIMIT = 8
SEARCH_LIMIT = 10
PROJECTS_TTL_SECONDS = 600.0
RECENT_PROJECTS = 10
# How many of a member's latest issues are read to find the projects they are in.
RECENT_PROJECT_ISSUES = 30

_ISSUE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,15}-\d{1,7}$")
_BROWSE_KEY = re.compile(r"/browse/([A-Za-z][A-Za-z0-9_]{1,15}-\d{1,7})(?:[/?#]|$)")
_ISSUE_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9 \-]{0,40}$")

_projects = TTLCache(max_entries=128)


class JiraApi:
    def __init__(self, ctx: SourceContext) -> None:
        section = ctx.config.get("jira") if isinstance(ctx.config.get("jira"), dict) else {}
        if not section.get("enabled"):
            raise SourceNotConfigured("jira")
        usable = [item for item in (section.get("instances") or []) if isinstance(item, dict) and item.get("enabled") is not False]
        instance = connector_http.first_auth_instance(usable)
        if not instance:
            raise SourceNotConfigured("jira")
        self.ctx = ctx
        self.base = str(instance.get("url") or "").strip().rstrip("/")
        self.version = "3" if str(instance.get("api_version") or "") == "3" else "2"
        self.api = f"{self.base}/rest/api/{self.version}"
        self.headers = {**connector_http.instance_auth_headers(instance), "Accept": "application/json"}
        self.instance_setting = instance.get("proxy")

    async def get(self, path: str, params: dict | None = None):
        return await self.ctx.http.get(
            "jira", f"{self.api}{path}", headers=self.headers, params=params, instance_setting=self.instance_setting
        )

    async def identity(self) -> Identity:
        return await atlassian_identity(
            self.ctx,
            connector="jira",
            whoami_url=f"{self.api}/myself",
            lookup_url=f"{self.api}/user",
            headers=self.headers,
            instance_setting=self.instance_setting,
        )

    async def search(self, jql: str, limit: int) -> list[dict]:
        """Issues for a JQL, or nothing when this server rejects the query.

        A 400 is how Jira says "no such issue key" and "this function does not
        exist here"; neither is an outage, so both read as an empty group.
        """
        path = "/search/jql" if self.version == "3" else "/search"
        response = await self.get(path, {"jql": jql, "maxResults": str(limit), "fields": ISSUE_FIELDS})
        if response.status_code == 400 or response.status_code == 404:
            return []
        if response.status_code != 200:
            raise SourceUnavailable("error")
        data = json_of(response)
        issues = data.get("issues") if isinstance(data, dict) else None
        return [issue for issue in issues if isinstance(issue, dict)] if isinstance(issues, list) else []

    def item(self, issue: dict) -> Item | None:
        key = str(issue.get("key") or "").strip()
        if not _ISSUE_KEY.match(key):
            return None
        fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
        status = fields.get("status") if isinstance(fields.get("status"), dict) else {}
        issue_type = fields.get("issuetype") if isinstance(fields.get("issuetype"), dict) else {}
        project = fields.get("project") if isinstance(fields.get("project"), dict) else {}
        extra = {"key": key}
        if project.get("key"):
            extra["project"] = str(project.get("key"))
        if issue_type.get("name"):
            extra["type"] = str(issue_type.get("name"))
        return Item(
            value=key,
            title=key,
            subtitle=str(fields.get("summary") or "").strip(),
            meta=str(fields.get("updated") or "").strip(),
            badge=str(status.get("name") or "").strip(),
            url=f"{self.base}/browse/{key}",
            fields=extra,
        )

    def items(self, issues: list[dict], seen: set[str] | None = None) -> list[Item]:
        out: list[Item] = []
        for issue in issues:
            item = self.item(issue)
            if item is None or (seen is not None and item.value in seen):
                continue
            if seen is not None:
                seen.add(item.value)
            out.append(item)
        return out


def _type_filter(ctx: SourceContext) -> str:
    types = [value.strip() for value in ctx.param_list("types") if _ISSUE_TYPE.match(value.strip())][:5]
    if not types:
        return ""
    quoted = ", ".join(f'"{value}"' for value in types)
    return f" AND issuetype in ({quoted})"


def issue_key_in(text: str, base_url: str = "") -> str:
    """The issue key a member typed or pasted: a bare key, or a browse URL of
    this Jira."""
    cleaned = (text or "").strip()
    if _ISSUE_KEY.match(cleaned):
        return cleaned.upper()
    match = _BROWSE_KEY.search(cleaned)
    if match and (not base_url or urlparse(cleaned).hostname == urlparse(base_url).hostname):
        return match.group(1).upper()
    return ""


async def issues(ctx: SourceContext) -> Result:
    api = JiraApi(ctx)
    identity = await api.identity()
    type_filter = _type_filter(ctx)
    query = ctx.q.strip()

    if len(query) >= 2:
        key = issue_key_in(query, api.base)
        if key:
            # An exact key is found whatever its type: the member named it.
            found = await api.search(f'issuekey = "{key}"', 1)
        else:
            words = search_words(query)
            if not words:
                return Result("jira.issues", identity=identity.mode)
            text = " ".join(words) + "*"
            found = await api.search(f'summary ~ "{text}"{type_filter} ORDER BY updated DESC', SEARCH_LIMIT)
        return Result("jira.issues", groups=[Group("results", api.items(found))], identity=identity.mode)

    seen: set[str] = set()

    async def viewed() -> Group:
        found = await api.search(f"issuekey in issueHistory(){type_filter} ORDER BY lastViewed DESC", GROUP_LIMIT)
        return Group("viewed", api.items(found))

    async def by_field(group_id: str, field_name: str, who: str) -> Group:
        jql = f"{field_name} = {who} AND resolution = Unresolved{type_filter} ORDER BY updated DESC"
        return Group(group_id, api.items(await api.search(jql, GROUP_LIMIT)))

    if identity.personal:
        loaders = [viewed(), by_field("assigned", "assignee", "currentUser()")]
    elif identity.login:
        who = f'"{identity.login}"'
        loaders = [by_field("assigned", "assignee", who), by_field("reported", "reporter", who)]
    else:
        return Result("jira.issues", identity=identity.mode)

    groups = await gather_groups(*loaders)
    # A card that is about doing the work leads with what is assigned.
    if ctx.param("lead") == "assigned":
        groups.sort(key=lambda group: 0 if group.id == "assigned" else 1)
    # The same issue is listed once, in the first group that has it.
    for group in groups:
        group.items = [item for item in group.items if item.value not in seen and not seen.add(item.value)]
    return Result("jira.issues", groups=groups, identity=identity.mode)


def _project_item(api: JiraApi, project: Any) -> Item | None:
    if not isinstance(project, dict):
        return None
    key = str(project.get("key") or "").strip()
    if not key:
        return None
    return Item(
        value=key,
        title=key,
        subtitle=str(project.get("name") or "").strip(),
        url=f"{api.base}/browse/{key}",
        fields={"project": key},
    )


async def projects(ctx: SourceContext) -> Result:
    api = JiraApi(ctx)
    query = ctx.q.strip().lower()

    async def load_all() -> list[Item]:
        response = await api.get("/project")
        data = json_of(response) if response.status_code == 200 else None
        if not isinstance(data, list):
            raise SourceUnavailable("error")
        return [item for item in (_project_item(api, row) for row in data) if item is not None]

    mode = ""

    async def recent() -> Group | None:
        """The projects the member used lately.

        Jira keeps "recent projects" per account, so its own list is only
        the member's when the account is. For any other account the member's
        projects are the ones their latest issues are in.
        """
        nonlocal mode
        identity = await api.identity()
        mode = identity.mode
        if identity.personal:
            response = await api.get("/project", {"recent": str(RECENT_PROJECTS)})
            data = json_of(response) if response.status_code == 200 else None
            rows = data if isinstance(data, list) else []
        elif identity.login:
            who = f'"{identity.login}"'
            found = await api.search(f"(assignee = {who} OR reporter = {who}) ORDER BY updated DESC", RECENT_PROJECT_ISSUES)
            rows = [(issue.get("fields") or {}).get("project") for issue in found if isinstance(issue.get("fields"), dict)]
        else:
            return None
        items: list[Item] = []
        listed: set[str] = set()
        for row in rows:
            item = _project_item(api, row)
            if item is not None and item.value not in listed:
                listed.add(item.value)
                items.append(item)
        return Group("recent", items[:RECENT_PROJECTS])

    async def everything() -> Group:
        key = (getattr(ctx.user, "id", None), ctx.revision, api.base)
        rows = await _projects.get_or_load(key, load_all, lambda _value: PROJECTS_TTL_SECONDS)
        if query:
            starts = [item for item in rows if item.value.lower().startswith(query)]
            contains = [item for item in rows if item not in starts and query in f"{item.value} {item.subtitle}".lower()]
            return Group("results", starts + contains)
        return Group("all", list(rows))

    if query:
        groups = await gather_groups(everything())
    else:
        groups = await gather_groups(recent(), everything())
        listed = {item.value for group in groups if group.id == "recent" for item in group.items}
        for group in groups:
            if group.id == "all":
                group.items = [item for item in group.items if item.value not in listed]
    return Result("jira.projects", groups=groups, identity=mode)
