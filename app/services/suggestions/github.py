"""GitHub: the pull requests, branches, repositories and paths a member works with.

Read with the member's GitHub connector (github.com, GitHub Enterprise Cloud
with managed users, or GitHub Enterprise Server through its API base URL).

"Branches I pushed lately" has no single endpoint. The member's own event feed
names every push with its repository and ref; one GraphQL request then says,
for those few branches, whether each still exists, what the repository's
default branch is, and whether a pull request is already open. The feed is not
instant (GitHub documents a delay), so a branch pushed seconds ago can be
missing: the picker's text box and the "owner/repo" search cover that.
"""
from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from typing import Any
from urllib.parse import quote, urlparse

from app.services.suggestions.base import (
    Group,
    Item,
    Result,
    SourceContext,
    SourceNotConfigured,
    SourceUnavailable,
    gather_groups,
    json_of,
)
from app.services.suggestions.cache import TTLCache
from app.services.suggestions.identity import Identity, github_identity

DEFAULT_API_BASE = "https://api.github.com"
GROUP_LIMIT = 8
SEARCH_LIMIT = 10
BRANCH_CANDIDATES = 10
REPO_BRANCH_LIMIT = 15
PATH_LIMIT = 30
TREE_TTL_SECONDS = 600.0
MAX_TREE_ENTRIES = 20000

_REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,99}/[A-Za-z0-9._\-]{1,100}$")
_PR_URL = re.compile(r"^https?://[^\s/]+/[^\s/]+/[^\s/]+/pull/\d+(?:[/?#]\S*)?$")
_REPO_QUERY = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._\-]{0,99}/[A-Za-z0-9._\-]{1,100})(?:[\s:#@]+(\S.*))?$")

_trees = TTLCache(max_entries=64)


class GithubApi:
    def __init__(self, ctx: SourceContext) -> None:
        section = ctx.config.get("github") if isinstance(ctx.config.get("github"), dict) else {}
        token = str(section.get("api_token") or "").strip()
        if not section.get("enabled") or not token:
            raise SourceNotConfigured("github")
        self.ctx = ctx
        self.base = str(section.get("base_url") or DEFAULT_API_BASE).strip().rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    @property
    def graphql_url(self) -> str:
        parsed = urlparse(self.base)
        # GitHub Enterprise Server: REST at /api/v3, GraphQL at /api/graphql.
        if parsed.path.rstrip("/").endswith("/api/v3"):
            return self.base[: -len("/v3")] + "/graphql"
        return f"{self.base}/graphql"

    async def get(self, path: str, params: Any = None):
        return await self.ctx.http.get("github", f"{self.base}{path}", headers=self.headers, params=params)

    async def get_json(self, path: str, params: Any = None) -> Any:
        response = await self.get(path, params)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise SourceUnavailable("error")
        return json_of(response)

    async def graphql(self, query: str, variables: dict) -> dict:
        """The ``data`` of a GraphQL answer. Partial answers are normal here
        (a repository the token cannot see comes back null with an error
        beside it), so errors alone do not fail the request."""
        response = await self.ctx.http.request(
            "github", "POST", self.graphql_url, headers=self.headers, json_body={"query": query, "variables": variables}
        )
        payload = json_of(response) if response.status_code == 200 else None
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise SourceUnavailable("error")
        return data

    async def identity(self) -> Identity:
        return await github_identity(self.ctx, self.base, self.headers)

    async def search_issues(self, query: str, limit: int) -> list[dict]:
        data = await self.get_json(
            "/search/issues", {"q": query, "sort": "updated", "order": "desc", "per_page": str(limit)}
        )
        items = data.get("items") if isinstance(data, dict) else None
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _repo_of_api_url(url: str) -> str:
    marker = "/repos/"
    index = (url or "").find(marker)
    return url[index + len(marker):].strip("/") if index >= 0 else ""


def _pull_item(issue: dict) -> Item | None:
    url = str(issue.get("html_url") or "").strip()
    number = issue.get("number")
    if not url or not isinstance(number, int):
        return None
    repo = _repo_of_api_url(str(issue.get("repository_url") or ""))
    return Item(
        value=url,
        title=str(issue.get("title") or "").strip() or f"#{number}",
        subtitle=f"{repo}#{number}" if repo else f"#{number}",
        meta=str(issue.get("updated_at") or "").strip(),
        status="draft" if issue.get("draft") else "",
        url=url,
        fields={"repo": repo, "number": str(number)},
    )


def _pull_items(issues: list[dict]) -> list[Item]:
    return [item for item in (_pull_item(issue) for issue in issues) if item is not None]


async def pull_requests(ctx: SourceContext) -> Result:
    api = GithubApi(ctx)
    identity = await api.identity()
    me = "@me" if identity.personal else identity.login
    query = ctx.q.strip()

    if len(query) >= 2:
        if _PR_URL.match(query):
            # A pasted link is the answer already; nothing to look up.
            return Result("github.pull_requests", identity=identity.mode)
        scope = f"involves:{me} " if me else ""
        found = await api.search_issues(f"is:pr is:open {scope}in:title {query[:100]}", SEARCH_LIMIT)
        return Result("github.pull_requests", groups=[Group("results", _pull_items(found))], identity=identity.mode)

    if not me:
        return Result("github.pull_requests", identity=identity.mode)

    async def group(group_id: str, qualifier: str) -> Group:
        found = await api.search_issues(f"is:pr is:open {qualifier}:{me} archived:false", GROUP_LIMIT)
        return Group(group_id, _pull_items(found))

    groups = await gather_groups(group("review_requested", "review-requested"), group("mine", "author"))
    return Result("github.pull_requests", groups=groups, identity=identity.mode)


# --------------------------------------------------------------------------
# Branches
# --------------------------------------------------------------------------

_REF_FRAGMENT = (
    "name target { ... on Commit { committedDate } } "
    "associatedPullRequests(first: 5, orderBy: {field: UPDATED_AT, direction: DESC}) "
    "{ nodes { number url state isDraft } }"
)


def pushed_branches(events: Any, limit: int = BRANCH_CANDIDATES) -> list[tuple[str, str, str]]:
    """``(repository, branch, when)`` for the branches a member's event feed
    shows them pushing, newest first.

    The feed is newest first, so a branch deletion seen before any push of the
    same branch means the branch is gone; a push seen first means it was
    pushed again afterwards.
    """
    found: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    deleted: set[tuple[str, str]] = set()
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        repo = str((event.get("repo") or {}).get("name") or "").strip() if isinstance(event.get("repo"), dict) else ""
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        kind = event.get("type")
        branch = ""
        if kind == "PushEvent":
            ref = str(payload.get("ref") or "")
            branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ""
        elif kind == "CreateEvent" and payload.get("ref_type") == "branch":
            branch = str(payload.get("ref") or "")
        elif kind == "DeleteEvent" and payload.get("ref_type") == "branch":
            key = (repo, str(payload.get("ref") or ""))
            if key not in seen:
                deleted.add(key)
            continue
        if not branch or not _REPO_NAME.match(repo):
            continue
        key = (repo, branch)
        if key in seen or key in deleted:
            continue
        seen.add(key)
        found.append((repo, branch, str(event.get("created_at") or "")))
        if len(found) >= limit:
            break
    return found


def _branch_details_query(candidates: list[tuple[str, str, str]]) -> tuple[str, dict, list[tuple[str, list[str]]]]:
    """One query for every candidate branch, grouped by repository.

    Names travel as variables, never inside the query text, so a branch called
    ``") { viewer`` is a string and nothing else.
    """
    by_repo: "OrderedDict[str, list[str]]" = OrderedDict()
    for repo, branch, _when in candidates:
        by_repo.setdefault(repo, []).append(branch)
    definitions: list[str] = []
    selections: list[str] = []
    variables: dict[str, str] = {}
    for repo_index, (repo, branches) in enumerate(by_repo.items()):
        owner, name = repo.split("/", 1)
        definitions += [f"$o{repo_index}: String!", f"$n{repo_index}: String!"]
        variables[f"o{repo_index}"] = owner
        variables[f"n{repo_index}"] = name
        refs = []
        for branch_index, branch in enumerate(branches):
            variable = f"b{repo_index}_{branch_index}"
            definitions.append(f"${variable}: String!")
            variables[variable] = f"refs/heads/{branch}"
            refs.append(f"b{branch_index}: ref(qualifiedName: ${variable}) {{ {_REF_FRAGMENT} }}")
        selections.append(
            f"r{repo_index}: repository(owner: $o{repo_index}, name: $n{repo_index}) "
            f"{{ nameWithOwner url isArchived defaultBranchRef {{ name }} {' '.join(refs)} }}"
        )
    query = f"query({', '.join(definitions)}) {{ {' '.join(selections)} }}"
    return query, variables, list(by_repo.items())


def branch_value(repo: str, branch: str, base: str = "") -> str:
    """What the prompt says about a picked branch. Identifiers only."""
    return f"branch {branch} of {repo} (base {base})" if base else f"branch {branch} of {repo}"


def _branch_item(repo: dict, ref: dict, when: str = "") -> tuple[str, Item] | None:
    """``(group id, row)`` for one branch, or None when it should not be offered:
    the default branch, or one whose pull request was already merged."""
    name = str(ref.get("name") or "").strip()
    full_name = str(repo.get("nameWithOwner") or "").strip()
    default = str(((repo.get("defaultBranchRef") or {}) if isinstance(repo.get("defaultBranchRef"), dict) else {}).get("name") or "")
    if not name or not full_name or name == default:
        return None
    pulls = ref.get("associatedPullRequests") if isinstance(ref.get("associatedPullRequests"), dict) else {}
    nodes = [node for node in (pulls.get("nodes") or []) if isinstance(node, dict)]
    open_pull = next((node for node in nodes if node.get("state") == "OPEN"), None)
    if open_pull is None and any(node.get("state") == "MERGED" for node in nodes):
        return None
    target = ref.get("target") if isinstance(ref.get("target"), dict) else {}
    repo_url = str(repo.get("url") or "").rstrip("/")
    item = Item(
        value=branch_value(full_name, name, default),
        title=name,
        subtitle=full_name,
        meta=str(target.get("committedDate") or when or ""),
        url=f"{repo_url}/tree/{quote(name, safe='/')}" if repo_url else "",
        fields={"repo": full_name, "branch": name, "base": default},
    )
    if open_pull is not None:
        item.badge = f"#{open_pull.get('number')}"
        item.status = "draft" if open_pull.get("isDraft") else "open"
        item.url = str(open_pull.get("url") or item.url)
        return "has_pr", item
    return "no_pr", item


async def _pushed_groups(api: GithubApi, login: str) -> list[Group]:
    events = await api.get_json(f"/users/{quote(login, safe='')}/events", {"per_page": "100"})
    candidates = pushed_branches(events)
    if not candidates:
        return []
    query, variables, layout = _branch_details_query(candidates)
    when_of = {(repo, branch): when for repo, branch, when in candidates}
    try:
        data = await api.graphql(query, variables)
    except SourceUnavailable:
        # GraphQL is closed to some tokens. The feed alone still names the
        # branches; what it cannot say is whether one already has a pull request.
        return [
            Group(
                "pushed",
                [
                    Item(
                        value=branch_value(repo, branch),
                        title=branch,
                        subtitle=repo,
                        meta=when,
                        fields={"repo": repo, "branch": branch},
                    )
                    for repo, branch, when in candidates
                ],
            )
        ]

    groups = {"no_pr": Group("no_pr"), "has_pr": Group("has_pr")}
    for repo_index, (repo_name, branches) in enumerate(layout):
        repo = data.get(f"r{repo_index}")
        if not isinstance(repo, dict) or repo.get("isArchived"):
            continue
        for branch_index, branch in enumerate(branches):
            ref = repo.get(f"b{branch_index}")
            if not isinstance(ref, dict):
                continue
            placed = _branch_item(repo, ref, when_of.get((repo_name, branch), ""))
            if placed is not None:
                groups[placed[0]].items.append(placed[1])
    # Events put the candidates newest first, but they were regrouped by
    # repository for the query; the commit date restores the order.
    for group in groups.values():
        group.items.sort(key=lambda item: item.meta, reverse=True)
    return [groups["no_pr"], groups["has_pr"]]


_REPO_BRANCHES_QUERY = (
    "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { "
    "nameWithOwner url isArchived defaultBranchRef { name } "
    'refs(refPrefix: "refs/heads/", first: 50, orderBy: {field: TAG_COMMIT_DATE, direction: DESC}) '
    f"{{ nodes {{ {_REF_FRAGMENT} }} }} }} }}"
)


async def _repository_branches(api: GithubApi, repo_name: str, needle: str) -> Group:
    owner, name = repo_name.split("/", 1)
    data = await api.graphql(_REPO_BRANCHES_QUERY, {"owner": owner, "name": name})
    repo = data.get("repository")
    if not isinstance(repo, dict):
        return Group("results")
    refs = repo.get("refs") if isinstance(repo.get("refs"), dict) else {}
    items: list[Item] = []
    for ref in refs.get("nodes") or []:
        if not isinstance(ref, dict):
            continue
        if needle and needle.lower() not in str(ref.get("name") or "").lower():
            continue
        placed = _branch_item(repo, ref)
        if placed is None:
            continue
        items.append(placed[1])
        if len(items) >= REPO_BRANCH_LIMIT:
            break
    return Group("results", items)


async def _workspace_group(ctx: SourceContext) -> Group | None:
    """Branches checked out in the selected assistant's workspace that carry
    work a pull request could show: commits ahead of their base, or changes
    not yet committed. The assistant made these; no API knows about them until
    they are pushed."""
    if ctx.runtime_get is None:
        return None
    try:
        status, data = await ctx.runtime_get("api/workspace/repos", [])
    except Exception:
        return None
    repos = data.get("repos") if status == 200 and isinstance(data, dict) else None
    items: list[Item] = []
    for repo in repos if isinstance(repos, list) else []:
        if not isinstance(repo, dict):
            continue
        name = str(repo.get("repo") or "").strip()
        branch = str(repo.get("branch") or "").strip()
        default = str(repo.get("default_branch") or "").strip()
        path = str(repo.get("path") or "").strip()
        if not _REPO_NAME.match(name) or not branch or branch == default:
            continue
        ahead = repo.get("ahead") if isinstance(repo.get("ahead"), int) else None
        dirty = repo.get("dirty") is True
        if not dirty and not ahead:
            continue
        value = branch_value(name, branch, default)
        if path:
            value += f", checked out at {path} in the workspace"
        items.append(
            Item(
                value=value,
                title=branch,
                subtitle=f"{name} ({path})" if path else name,
                meta=str(repo.get("last_commit_at") or ""),
                status="uncommitted" if dirty else "ahead",
                count=None if dirty else ahead,
                fields={"repo": name, "branch": branch, "base": default, "path": path},
            )
        )
    return Group("workspace", items) if items else None


async def branches(ctx: SourceContext) -> Result:
    api = GithubApi(ctx)
    query = ctx.q.strip()

    match = _REPO_QUERY.match(query) if len(query) >= 3 else None
    if match:
        identity = await api.identity()
        group = await _repository_branches(api, match.group(1), (match.group(2) or "").strip())
        return Result("github.branches", groups=[group], identity=identity.mode)

    # The workspace is asked while GitHub is: three round trips to GitHub in
    # a row are slow enough without a fourth wait in front of them.
    workspace_task = asyncio.ensure_future(_workspace_group(ctx))
    try:
        identity = await api.identity()
    except BaseException:
        workspace_task.cancel()
        raise
    pushed: list[Group] = []
    failure: SourceUnavailable | None = None
    if identity.login:
        try:
            pushed = await _pushed_groups(api, identity.login)
        except SourceUnavailable as exc:
            failure = exc
    workspace = await workspace_task
    if failure is not None and workspace is None:
        raise failure
    groups = ([workspace] if workspace else []) + pushed
    # A branch checked out in the workspace is listed there, not twice.
    listed = {(item.fields.get("repo"), item.fields.get("branch")) for item in (workspace.items if workspace else [])}
    for group in pushed:
        group.items = [item for item in group.items if (item.fields.get("repo"), item.fields.get("branch")) not in listed]
    return Result("github.branches", groups=groups, identity=identity.mode)


# --------------------------------------------------------------------------
# Repositories and paths
# --------------------------------------------------------------------------


def _repo_item(repo: Any) -> Item | None:
    if not isinstance(repo, dict):
        return None
    full_name = str(repo.get("full_name") or "").strip()
    if not _REPO_NAME.match(full_name) or repo.get("archived"):
        return None
    return Item(
        value=full_name,
        title=full_name,
        subtitle=str(repo.get("description") or "").strip(),
        meta=str(repo.get("pushed_at") or "").strip(),
        url=str(repo.get("html_url") or "").strip(),
        fields={"repo": full_name, "base": str(repo.get("default_branch") or "")},
    )


async def repos(ctx: SourceContext) -> Result:
    api = GithubApi(ctx)
    identity = await api.identity()
    query = ctx.q.strip()

    if len(query) >= 2:
        if _REPO_NAME.match(query):
            exact = _repo_item(await api.get_json(f"/repos/{quote(query, safe='/')}"))
            if exact is not None:
                return Result("github.repos", groups=[Group("results", [exact])], identity=identity.mode)
        data = await api.get_json(
            "/search/repositories", {"q": f"{query[:100]} in:name fork:true", "per_page": str(SEARCH_LIMIT)}
        )
        rows = data.get("items") if isinstance(data, dict) else []
        items = [item for item in (_repo_item(row) for row in rows or []) if item is not None]
        return Result("github.repos", groups=[Group("results", items)], identity=identity.mode)

    if identity.personal:
        rows = await api.get_json("/user/repos", {"sort": "pushed", "per_page": "20"})
    elif identity.login:
        rows = await api.get_json(f"/users/{quote(identity.login, safe='')}/repos", {"sort": "pushed", "per_page": "20"})
    else:
        rows = []
    items = [item for item in (_repo_item(row) for row in rows or []) if item is not None]
    return Result("github.repos", groups=[Group("recent", items)], identity=identity.mode)


async def _tree(api: GithubApi, repo: str) -> list[tuple[str, bool]]:
    """``(path, is directory)`` for every entry of a repository's default
    branch, kept for a while: a tree is large and does not change by the minute."""
    key = (getattr(api.ctx.user, "id", None), api.ctx.revision, api.base, repo.lower())

    async def load() -> list[tuple[str, bool]]:
        info = await api.get_json(f"/repos/{quote(repo, safe='/')}")
        branch = str((info or {}).get("default_branch") or "").strip() if isinstance(info, dict) else ""
        if not branch:
            return []
        data = await api.get_json(
            f"/repos/{quote(repo, safe='/')}/git/trees/{quote(branch, safe='')}", {"recursive": "1"}
        )
        entries = data.get("tree") if isinstance(data, dict) else None
        out: list[tuple[str, bool]] = []
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and entry.get("path") and entry.get("type") in ("blob", "tree"):
                out.append((str(entry["path"]), entry["type"] == "tree"))
                if len(out) >= MAX_TREE_ENTRIES:
                    break
        return out

    return await _trees.get_or_load(key, load, lambda _value: TREE_TTL_SECONDS)


def match_paths(entries: list[tuple[str, bool]], query: str, limit: int = PATH_LIMIT) -> list[tuple[str, bool]]:
    """Paths for a typed fragment: top-level entries when nothing is typed,
    otherwise names that contain it before paths that merely pass through it."""
    needle = query.strip().lower()
    if not needle:
        return [entry for entry in entries if "/" not in entry[0]][:limit]
    by_name: list[tuple[str, bool]] = []
    by_path: list[tuple[str, bool]] = []
    for entry in entries:
        lowered = entry[0].lower()
        if needle not in lowered:
            continue
        (by_name if needle in lowered.rsplit("/", 1)[-1] else by_path).append(entry)
        if len(by_name) >= limit:
            break
    return (by_name + by_path)[:limit]


async def paths(ctx: SourceContext) -> Result:
    api = GithubApi(ctx)
    repo = ctx.param("repo").strip()
    if not _REPO_NAME.match(repo):
        # No repository chosen yet: nothing to list, and nothing wrong either.
        return Result("github.paths")
    entries = await _tree(api, repo)
    items = [
        Item(value=path, title=path, status="folder" if is_dir else "", fields={"path": path, "repo": repo})
        for path, is_dir in match_paths(entries, ctx.q)
    ]
    return Result("github.paths", groups=[Group("results", items)])
