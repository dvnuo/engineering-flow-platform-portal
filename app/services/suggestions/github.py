"""GitHub: the pull requests, branches, repositories and paths a member works with.

Read with the member's GitHub connector (github.com, GitHub Enterprise Cloud
with managed users, or GitHub Enterprise Server through its API base URL).

"My recent branches" has no single endpoint, and nothing records who created
a branch. What GitHub does keep is each repository's activity log: every
push, branch creation and deletion with who did it, as it happens. So the
branches are found in two steps. First the repositories the member works in:
the ones their recent pull requests are in, the ones their event feed names,
and their own. Then, for the few most recent of those, the activity log
filtered to the member. One GraphQL request finally says, for the branches
left standing, whether each still exists, what the repository's default
branch is, and whether a pull request is already open.

The event feed alone is not enough, and was measured before it was demoted:
six hours after eight branches were pushed it listed one of them, and its
rows do not come in order. It still contributes, because it can name a
repository the member has no pull request in yet.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
BRANCH_CANDIDATES = 12
REPO_BRANCH_LIMIT = 15
# How many of the member's repositories have their activity log read, and how
# far back: one request each, side by side.
ACTIVITY_REPOS = 6
ACTIVITY_PERIOD = "quarter"
RECENT_REPOS = 20
# GitHub is asked to confirm the list only while it can answer by this many
# seconds after the picker asked; later than that the list is shown as found.
CONFIRM_BY_SECONDS = 5.0
MIN_CONFIRM_SECONDS = 0.5
# What the member did in their repositories is read until this long after the
# picker asked, leaving time to answer before the picker gives up.
OWN_BY_SECONDS = 6.5
# The feed and the member's own repositories add to the pull requests; they
# are not waited for as long.
SECONDARY_LISTING_SECONDS = 3.0
SECONDARY_GRACE_SECONDS = 0.8
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
# Where the member works
# --------------------------------------------------------------------------

# One thing that happened to a branch: (repository, branch, when, still there).
Mark = tuple[str, str, str, bool]

WORK_TTL_SECONDS = 60.0
WORK_TTL_PARTIAL_SECONDS = 15.0

_work = TTLCache(max_entries=256)


def clear_work() -> None:
    _work.clear()


def _stamp(value: Any) -> float:
    """A timestamp as a number, 0.0 for anything unreadable. The feed, the
    activity log and commits each spell theirs, and not always the same way."""
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def feed_marks(events: Any) -> list[Mark]:
    """What a member's event feed says happened to branches."""
    marks: list[Mark] = []
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        repo = str((event.get("repo") or {}).get("name") or "").strip() if isinstance(event.get("repo"), dict) else ""
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        kind = event.get("type")
        if kind == "PushEvent":
            ref = str(payload.get("ref") or "")
            branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ""
            alive = True
        elif kind in ("CreateEvent", "DeleteEvent") and payload.get("ref_type") == "branch":
            branch = str(payload.get("ref") or "")
            alive = kind == "CreateEvent"
        else:
            continue
        if branch and _REPO_NAME.match(repo):
            marks.append((repo, branch, str(event.get("created_at") or ""), alive))
    return marks


_ACTIVITY_ALIVE = frozenset({"push", "force_push", "branch_creation"})


def activity_marks(repo: str, rows: Any) -> list[Mark]:
    """What a repository's activity log says one member did to its branches.

    A merged pull request is in the log too, as a write to the branch it was
    merged into. That branch is not the member's work and is left out.
    """
    marks: list[Mark] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        ref = str(row.get("ref") or "")
        kind = row.get("activity_type")
        if not ref.startswith("refs/heads/"):
            continue
        if kind in _ACTIVITY_ALIVE:
            alive = True
        elif kind == "branch_deletion":
            alive = False
        else:
            continue
        marks.append((repo, ref[len("refs/heads/"):], str(row.get("timestamp") or ""), alive))
    return marks


def recent_branches(marks: list[Mark], limit: int = BRANCH_CANDIDATES) -> list[tuple[str, str, str]]:
    """``(repository, branch, when)`` for the branches still standing, the one
    touched last first.

    The last thing that happened to a branch decides: deleted after its last
    push, it is gone; pushed again after a deletion, it is back. The marks
    may come in any order (the event feed's do), and the same push may be in
    them twice, once from the feed and once from the activity log.
    """
    latest: dict[tuple[str, str], tuple[float, bool, str, str]] = {}
    for repo, branch, when, alive in marks:
        key = (repo.lower(), branch)
        stamp = _stamp(when)
        known = latest.get(key)
        # Within one second a deletion wins: a slot spent on a dead branch is
        # a slot a live one does not get.
        if known is None or stamp > known[0] or (stamp == known[0] and known[1] and not alive):
            latest[key] = (stamp, alive, when, repo)
    standing = sorted((entry for entry in latest.items() if entry[1][1]), key=lambda entry: entry[1][0], reverse=True)
    return [(value[3], key[1], value[2]) for key, value in standing[:limit]]


@dataclass
class _Repo:
    """What the listings said about one repository."""

    name: str
    when: str = ""
    at: float = 0.0
    url: str = ""
    default: str = ""
    description: str = ""
    archived: bool = False


@dataclass
class _Work:
    """Where a member has been working.

    ``repos`` is newest first, without archived ones; ``by_name`` has every
    repository any listing named. ``pulls`` maps ``(repository, branch)`` to
    the member's pull requests from that branch, or is None when GitHub would
    not say which branch a pull request is for. ``bases`` are the branches
    their pull requests merge into: long-lived ones (a release line, a branch
    per environment) that the member writes to by merging, not work of
    theirs waiting for a pull request.
    """

    repos: list[_Repo] = field(default_factory=list)
    by_name: dict[str, _Repo] = field(default_factory=dict)
    marks: list[Mark] = field(default_factory=list)
    pulls: dict[tuple[str, str], list[dict]] | None = None
    bases: set[tuple[str, str]] = field(default_factory=set)
    complete: bool = True


# The member's pull requests: where they work, which branch each one is from
# and into, and what that repository's default branch is. Two searches in one
# request, because "the newest fifty" alone would lose a pull request that
# has been open for months, and its branch would then look like it had none.
_PULLS_QUERY = (
    "query($open: String!, $recent: String!) { "
    "open: search(query: $open, type: ISSUE, first: 50) { nodes { ...pull } } "
    "recent: search(query: $recent, type: ISSUE, first: 50) { nodes { ...pull } } } "
    "fragment pull on PullRequest { "
    "number url state isDraft updatedAt headRefName baseRefName headRepository { nameWithOwner } "
    "repository { nameWithOwner url isArchived defaultBranchRef { name } } }"
)


def _default_branch(repo: dict) -> str:
    ref = repo.get("defaultBranchRef")
    return str(ref.get("name") or "").strip() if isinstance(ref, dict) else ""


async def _members_work(api: GithubApi, identity: Identity) -> _Work:
    """The repositories a member works in, most recent first.

    Three listings, asked side by side, each allowed to fail on its own: the
    member's pull requests (current to the second, and the best sign of where
    their next branch goes), their event feed (hours late, but it names a
    repository they have no pull request in yet), and the repositories that
    are theirs. "Everything I can push to, by last push" is deliberately not
    asked: for a member of a large organization that is other people's work.
    """
    login = identity.login
    me = "@me" if identity.personal else login
    if not me or not login:
        return _Work()
    key = (getattr(api.ctx.user, "id", None), api.ctx.revision, api.base, identity.mode, login.lower())

    async def load() -> _Work:
        failures: list[SourceUnavailable] = []

        async def quiet(request: Any) -> Any:
            try:
                return await request
            except SourceUnavailable as exc:
                failures.append(exc)
            return None

        async def pull_requests() -> tuple[list[dict] | None, list[dict]]:
            try:
                data = await api.graphql(
                    _PULLS_QUERY,
                    {"open": f"is:pr is:open author:{me} sort:updated-desc", "recent": f"is:pr author:{me} sort:updated-desc"},
                )
            except SourceUnavailable:
                # GraphQL is closed to some tokens. The REST search still
                # names the repositories; it cannot name the branches.
                return None, await api.search_issues(f"is:pr author:{me}", 30)
            nodes: list[dict] = []
            seen: set[str] = set()
            for alias in ("recent", "open"):
                found = data.get(alias) if isinstance(data.get(alias), dict) else {}
                for node in found.get("nodes") or []:
                    if isinstance(node, dict) and node.get("url") not in seen:
                        seen.add(node.get("url"))
                        nodes.append(node)
            return nodes, []

        if identity.personal:
            own = api.get_json("/user/repos", {"affiliation": "owner,collaborator", "sort": "pushed", "per_page": "20"})
        else:
            own = api.get_json(f"/users/{quote(login, safe='')}/repos", {"sort": "pushed", "per_page": "20"})
        began = time.monotonic()
        asking = asyncio.ensure_future(quiet(pull_requests()))
        others = [
            asyncio.ensure_future(quiet(api.get_json(f"/users/{quote(login, safe='')}/events", {"per_page": "100"}))),
            asyncio.ensure_future(quiet(own)),
        ]
        try:
            found = await asking
            # The pull requests are the listing that matters. Once they are
            # in, the other two get a moment more and no longer; without
            # them, the other two are all there is and are waited for.
            patience = SECONDARY_LISTING_SECONDS - (time.monotonic() - began)
            if found is not None:
                patience = min(patience, SECONDARY_GRACE_SECONDS)
            await asyncio.wait(others, timeout=max(0.05, patience))
        finally:
            for task in [asking, *others]:
                if not task.done():
                    task.cancel()
                    failures.append(SourceUnavailable("timeout"))
        events, rows = (task.result() if task.done() and not task.cancelled() else None for task in others)
        if len(failures) == 3:
            raise failures[0]
        nodes, issues = found if found is not None else (None, [])

        work = _Work(pulls=None if nodes is None else {}, complete=not failures)

        def note(name: str, when: str) -> _Repo | None:
            if not _REPO_NAME.match(name):
                return None
            repo = work.by_name.setdefault(name.lower(), _Repo(name))
            at = _stamp(when)
            if at > repo.at or not repo.when:
                repo.at, repo.when = max(at, repo.at), when
            return repo

        for node in nodes or []:
            described = node.get("repository") if isinstance(node.get("repository"), dict) else {}
            repo = note(str(described.get("nameWithOwner") or "").strip(), str(node.get("updatedAt") or ""))
            if repo is None:
                continue
            repo.url = str(described.get("url") or "").rstrip("/")
            repo.default = _default_branch(described)
            repo.archived = bool(described.get("isArchived"))
            head = node.get("headRepository") if isinstance(node.get("headRepository"), dict) else {}
            branch = str(node.get("headRefName") or "")
            if branch:
                source = str(head.get("nameWithOwner") or repo.name).lower()
                work.pulls.setdefault((source, branch), []).append(node)
            if node.get("baseRefName"):
                work.bases.add((repo.name.lower(), str(node.get("baseRefName"))))
        for issue in issues:
            note(_repo_of_api_url(str(issue.get("repository_url") or "")), str(issue.get("updated_at") or ""))
        work.marks = feed_marks(events)
        for name, _branch, when, _alive in work.marks:
            note(name, when)
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            repo = note(str(row.get("full_name") or "").strip(), str(row.get("pushed_at") or ""))
            if repo is None:
                continue
            repo.url = repo.url or str(row.get("html_url") or "").rstrip("/")
            repo.default = repo.default or str(row.get("default_branch") or "").strip()
            repo.description = str(row.get("description") or "").strip()
            repo.archived = repo.archived or bool(row.get("archived"))
        work.repos = sorted((repo for repo in work.by_name.values() if not repo.archived), key=lambda repo: repo.at, reverse=True)
        return work

    return await _work.get_or_load(key, load, lambda work: WORK_TTL_SECONDS if work.complete else WORK_TTL_PARTIAL_SECONDS)


async def _repository_activity(api: GithubApi, repo: str, login: str) -> list[Mark] | None:
    """The member's pushes, branch creations and deletions in one repository,
    or None when the log could not be read: a server without it (an older
    GitHub Enterprise Server), or a repository the token may not read. What
    the feed knows of that repository is then all there is."""
    try:
        response = await api.get(
            f"/repos/{quote(repo, safe='/')}/activity",
            {"actor": login, "time_period": ACTIVITY_PERIOD, "per_page": "100"},
        )
    except SourceUnavailable:
        return None
    rows = json_of(response) if response.status_code == 200 else None
    return activity_marks(repo, rows) if isinstance(rows, list) else None


async def _describe(api: GithubApi, repo: _Repo) -> None:
    """Fill in a repository the listings only named."""
    try:
        data = await api.get_json(f"/repos/{quote(repo.name, safe='/')}")
    except SourceUnavailable:
        return
    if not isinstance(data, dict):
        return
    repo.default = str(data.get("default_branch") or "").strip()
    repo.url = repo.url or str(data.get("html_url") or "").rstrip("/")
    repo.description = repo.description or str(data.get("description") or "").strip()
    repo.archived = bool(data.get("archived"))


# --------------------------------------------------------------------------
# Branches
# --------------------------------------------------------------------------

_REF_FRAGMENT = (
    "name target { ... on Commit { committedDate } } "
    "associatedPullRequests(first: 5, orderBy: {field: UPDATED_AT, direction: DESC}) "
    "{ nodes { number url state isDraft } }"
)


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


def branch_value(repo: str, branch: str, default: str = "") -> str:
    """What the prompt says about a picked branch. Identifiers only.

    The default branch is named as a fact about the repository, not as the
    pull request's target: where it merges is the member's to say. A card
    that asks has a field for it, and a picked row fills that field with
    this same default.
    """
    return f"branch {branch} of {repo} (default branch {default})" if default else f"branch {branch} of {repo}"


def _branch_row(
    repo: str, repo_url: str, default: str, branch: str, when: str, pulls: list[dict] | None
) -> tuple[str, Item] | None:
    """``(group id, row)`` for one branch, or None when it should not be offered:
    the default branch, or one whose pull request was already merged.

    ``pulls`` are the pull requests from this branch; None means nobody could
    say, and the branch is then listed without that claim.
    """
    if not branch or not repo or branch == default:
        return None
    fields = {"repo": repo, "branch": branch}
    if default:
        fields["base"] = default
    item = Item(
        value=branch_value(repo, branch, default),
        title=branch,
        subtitle=repo,
        meta=when,
        url=f"{repo_url}/tree/{quote(branch, safe='/')}" if repo_url else "",
        fields=fields,
    )
    if pulls is None:
        return "pushed", item
    open_pull = next((pull for pull in pulls if pull.get("state") == "OPEN"), None)
    if open_pull is None and any(pull.get("state") == "MERGED" for pull in pulls):
        return None
    if open_pull is not None:
        item.badge = f"#{open_pull.get('number')}"
        item.status = "draft" if open_pull.get("isDraft") else "open"
        item.url = str(open_pull.get("url") or item.url)
        return "has_pr", item
    return "no_pr", item


def _branch_item(repo: dict, ref: dict, when: str = "") -> tuple[str, Item] | None:
    """The same, from GitHub's own description of the repository and the branch.

    ``when`` is the member's own last push when it is known; a branch found
    by listing a repository has only its last commit's date.
    """
    pulls = ref.get("associatedPullRequests") if isinstance(ref.get("associatedPullRequests"), dict) else {}
    target = ref.get("target") if isinstance(ref.get("target"), dict) else {}
    return _branch_row(
        str(repo.get("nameWithOwner") or "").strip(),
        str(repo.get("url") or "").rstrip("/"),
        _default_branch(repo),
        str(ref.get("name") or "").strip(),
        str(when or target.get("committedDate") or ""),
        [node for node in (pulls.get("nodes") or []) if isinstance(node, dict)],
    )


async def _confirmed(api: GithubApi, candidates: list[tuple[str, str, str]], seconds: float) -> dict | None:
    """GitHub's word on each candidate: ``(repository, branch) -> (repository
    description, branch description)``, either of them None for a repository
    the token cannot see or a branch that is gone. None when GitHub did not
    answer in the time there was: the list is then shown as it stands."""
    if seconds < MIN_CONFIRM_SECONDS:
        return None
    query, variables, layout = _branch_details_query(candidates)
    try:
        data = await asyncio.wait_for(api.graphql(query, variables), timeout=seconds)
    except (SourceUnavailable, asyncio.TimeoutError):
        return None
    confirmed: dict[tuple[str, str], tuple[dict | None, dict | None]] = {}
    for repo_index, (repo_name, branches) in enumerate(layout):
        repo = data.get(f"r{repo_index}")
        repo = repo if isinstance(repo, dict) else None
        for branch_index, branch in enumerate(branches):
            ref = repo.get(f"b{branch_index}") if repo else None
            confirmed[(repo_name.lower(), branch)] = (repo, ref if isinstance(ref, dict) else None)
    return confirmed


async def _own_groups(api: GithubApi, identity: Identity, started: float) -> list[Group]:
    """The member's recent branches, split by whether a pull request is open.

    Two round trips make the list: where the member works, then what they
    did there. A third asks GitHub to confirm it (a colleague may have
    deleted a branch, or opened the pull request for it) and is only waited
    for while there is time: on a slow link the list as it stands is better
    than no list.
    """
    work = await _members_work(api, identity)
    top = work.repos[:ACTIVITY_REPOS]
    reading = [asyncio.ensure_future(_repository_activity(api, repo.name, identity.login)) for repo in top]
    # The feed names a repository and no more; its default branch decides
    # which of its branches may be offered at all.
    describing = [asyncio.ensure_future(_describe(api, repo)) for repo in top if not repo.default]
    marks: list[Mark] = []
    read: set[str] = set()
    if reading:
        # What answered in time is used; one slow repository does not hold
        # the list back.
        try:
            await asyncio.wait(reading + describing, timeout=max(MIN_CONFIRM_SECONDS, OWN_BY_SECONDS - (time.monotonic() - started)))
        finally:
            for task in reading + describing:
                task.cancel()
        answered = [task for task in reading if task.done() and not task.cancelled() and task.exception() is None]
        if not answered:
            # Not one repository answered in the time there was. An empty
            # list would say "you have no branches", which nobody found out.
            raise SourceUnavailable("timeout")
        for repo, task in zip(top, reading):
            if task in answered and task.result() is not None:
                read.add(repo.name.lower())
                marks += task.result()
    # A repository's own log is the whole story of what the member did there.
    # The feed is kept for the repositories whose log was not read: it is
    # late, and it shows a merge as a push to the branch merged into.
    marks += [mark for mark in work.marks if mark[0].lower() not in read]
    candidates = []
    for repo_name, branch, when in recent_branches(marks, BRANCH_CANDIDATES * 2):
        known = work.by_name.get(repo_name.lower())
        if known is not None and (known.archived or branch == known.default):
            continue
        if (repo_name.lower(), branch) in work.bases:
            continue
        candidates.append((repo_name, branch, when))
    del candidates[BRANCH_CANDIDATES:]
    if not candidates:
        return []

    confirmed = await _confirmed(api, candidates, CONFIRM_BY_SECONDS - (time.monotonic() - started))
    groups = {"no_pr": Group("no_pr"), "has_pr": Group("has_pr"), "pushed": Group("pushed")}
    for repo_name, branch, when in candidates:
        if confirmed is not None:
            repo, ref = confirmed.get((repo_name.lower(), branch), (None, None))
            placed = None if repo is None or ref is None or repo.get("isArchived") else _branch_item(repo, ref, when)
        else:
            known = work.by_name.get(repo_name.lower())
            if known is None or not known.default:
                # Nothing says which branch is this repository's default, so
                # nothing says this one is not it.
                continue
            mine = None if work.pulls is None else work.pulls.get((repo_name.lower(), branch), [])
            placed = _branch_row(known.name, known.url, known.default, branch, when, mine)
        if placed is not None:
            groups[placed[0]].items.append(placed[1])
    for group in groups.values():
        del group.items[GROUP_LIMIT:]
    return [groups["no_pr"], groups["has_pr"], groups["pushed"]]


# GitHub orders branches by name only (its commit-date order is for tags), so
# a page of them is read and put in order here. A typed fragment is matched
# by GitHub, across every branch the repository has.
_REPO_BRANCHES_QUERY = (
    "query($owner: String!, $name: String!, $needle: String) { repository(owner: $owner, name: $name) { "
    "nameWithOwner url isArchived defaultBranchRef { name } "
    'refs(refPrefix: "refs/heads/", first: 100, query: $needle) '
    f"{{ nodes {{ {_REF_FRAGMENT} }} }} }} }}"
)


async def _repository_branches(api: GithubApi, repo_name: str, needle: str) -> Group:
    owner, name = repo_name.split("/", 1)
    variables = {"owner": owner, "name": name}
    if needle:
        variables["needle"] = needle
    data = await api.graphql(_REPO_BRANCHES_QUERY, variables)
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
        if placed is not None:
            items.append(placed[1])
    items.sort(key=lambda item: _stamp(item.meta), reverse=True)
    return Group("results", items[:REPO_BRANCH_LIMIT])


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
    started = time.monotonic()

    match = _REPO_QUERY.match(query) if len(query) >= 3 else None
    if match:
        identity = await api.identity()
        group = await _repository_branches(api, match.group(1), (match.group(2) or "").strip())
        return Result("github.branches", groups=[group], identity=identity.mode)

    # The workspace is asked while GitHub is: the round trips to GitHub are
    # slow enough without another wait in front of them.
    workspace_task = asyncio.ensure_future(_workspace_group(ctx))
    try:
        identity = await api.identity()
    except BaseException:
        workspace_task.cancel()
        raise
    own: list[Group] = []
    failure: SourceUnavailable | None = None
    if identity.login:
        try:
            own = await _own_groups(api, identity, started)
        except SourceUnavailable as exc:
            failure = exc
    workspace = await workspace_task
    if failure is not None and workspace is None:
        raise failure
    groups = ([workspace] if workspace else []) + own
    # A branch checked out in the workspace is listed there, not twice.
    listed = {(item.fields.get("repo"), item.fields.get("branch")) for item in (workspace.items if workspace else [])}
    for group in own:
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

    # The repositories the member works in, not the ones pushed to last among
    # everything they can reach: in a large organization those are other
    # people's.
    work = await _members_work(api, identity)
    items = [
        Item(
            value=repo.name,
            title=repo.name,
            subtitle=repo.description,
            meta=repo.when,
            url=repo.url,
            fields={"repo": repo.name, **({"base": repo.default} if repo.default else {})},
        )
        for repo in work.repos[:RECENT_REPOS]
    ]
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
