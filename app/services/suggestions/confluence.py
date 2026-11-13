"""Confluence: the pages a member has been reading and editing.

Read with the member's Confluence connector, against the first instance that
has a complete credential. Server and Data Center serve the REST API at
/rest/api; a Cloud site (atlassian.net) serves it under /wiki.
"""
from __future__ import annotations

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
from app.services.suggestions.identity import Identity, atlassian_identity

GROUP_LIMIT = 8
SEARCH_LIMIT = 10


class ConfluenceApi:
    def __init__(self, ctx: SourceContext) -> None:
        section = ctx.config.get("confluence") if isinstance(ctx.config.get("confluence"), dict) else {}
        if not section.get("enabled"):
            raise SourceNotConfigured("confluence")
        usable = [item for item in (section.get("instances") or []) if isinstance(item, dict) and item.get("enabled") is not False]
        instance = connector_http.first_auth_instance(usable)
        if not instance:
            raise SourceNotConfigured("confluence")
        self.ctx = ctx
        self.base = str(instance.get("url") or "").strip().rstrip("/")
        host = urlparse(self.base).hostname or ""
        site = self.base
        if host.endswith(".atlassian.net") and not urlparse(self.base).path.rstrip("/").endswith("/wiki"):
            site = f"{self.base}/wiki"
        self.site = site
        self.api = f"{site}/rest/api"
        self.headers = {**connector_http.instance_auth_headers(instance), "Accept": "application/json"}
        self.instance_setting = instance.get("proxy")

    async def identity(self) -> Identity:
        return await atlassian_identity(
            self.ctx,
            connector="confluence",
            whoami_url=f"{self.api}/user/current",
            lookup_url=f"{self.api}/user",
            headers=self.headers,
            instance_setting=self.instance_setting,
        )

    async def search(self, cql: str, limit: int) -> list[Item]:
        """Pages for a CQL, or nothing when this server rejects the query (a
        function it does not have answers 400, which is not an outage)."""
        response = await self.ctx.http.get(
            "confluence",
            f"{self.api}/content/search",
            headers=self.headers,
            params={"cql": cql, "limit": str(limit), "expand": "space,version"},
            instance_setting=self.instance_setting,
        )
        if response.status_code in (400, 404):
            return []
        if response.status_code != 200:
            raise SourceUnavailable("error")
        data = json_of(response)
        if not isinstance(data, dict):
            return []
        links = data.get("_links") if isinstance(data.get("_links"), dict) else {}
        link_base = str(links.get("base") or self.site).rstrip("/")
        items: list[Item] = []
        for row in data.get("results") or []:
            item = self._item(row, link_base)
            if item is not None:
                items.append(item)
        return items

    def _item(self, row, link_base: str) -> Item | None:
        if not isinstance(row, dict):
            return None
        title = str(row.get("title") or "").strip()
        webui = str(((row.get("_links") or {}) if isinstance(row.get("_links"), dict) else {}).get("webui") or "").strip()
        page_id = str(row.get("id") or "").strip()
        if not title or not (webui or page_id):
            return None
        url = f"{link_base}{webui}" if webui.startswith("/") else f"{link_base}/pages/viewpage.action?pageId={page_id}"
        space = row.get("space") if isinstance(row.get("space"), dict) else {}
        version = row.get("version") if isinstance(row.get("version"), dict) else {}
        fields = {"id": page_id}
        if space.get("key"):
            fields["space"] = str(space.get("key"))
        return Item(
            value=url,
            title=title,
            subtitle=str(space.get("name") or space.get("key") or "").strip(),
            meta=str(version.get("when") or "").strip(),
            url=url,
            fields=fields,
        )


async def pages(ctx: SourceContext) -> Result:
    api = ConfluenceApi(ctx)
    identity = await api.identity()
    query = ctx.q.strip()

    if len(query) >= 2:
        if query.lower().startswith(("http://", "https://")):
            # A pasted link is the answer already.
            return Result("confluence.pages", identity=identity.mode)
        words = search_words(query)
        if not words:
            return Result("confluence.pages", identity=identity.mode)
        text = " ".join(words) + "*"
        found = await api.search(f'type = page AND title ~ "{text}" ORDER BY lastmodified DESC', SEARCH_LIMIT)
        return Result("confluence.pages", groups=[Group("results", found)], identity=identity.mode)

    async def viewed() -> Group:
        return Group("viewed", await api.search(f"type = page AND id in recentlyViewedContent({GROUP_LIMIT})", GROUP_LIMIT))

    async def edited(who: str) -> Group:
        cql = f"type = page AND contributor = {who} ORDER BY lastmodified DESC"
        return Group("edited", await api.search(cql, GROUP_LIMIT))

    if identity.personal:
        loaders = [viewed(), edited("currentUser()")]
    elif identity.login:
        loaders = [edited(f'"{identity.login}"')]
    else:
        return Result("confluence.pages", identity=identity.mode)

    groups = await gather_groups(*loaders)
    seen: set[str] = set()
    for group in groups:
        group.items = [item for item in group.items if item.value not in seen and not seen.add(item.value)]
    return Result("confluence.pages", groups=groups, identity=identity.mode)
