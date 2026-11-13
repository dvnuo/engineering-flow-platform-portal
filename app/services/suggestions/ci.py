"""Builds a member would want explained: the ones that just failed on them.

Jenkins has no "my builds" query, so there are two honest ways to find a
failing build without asking the member to paste its address:

- the checks on the member's own open pull requests. When CI reports its
  status to GitHub, a failed check carries the build's address.
- the jobs the member has asked about before. The page sends the values they
  used with the card; the job each one belongs to is asked for its latest
  failed builds.

Only a Jenkins address that belongs to one of the member's own configured
Jenkins instances is ever requested, so a value typed into the card cannot
point the Portal at another host.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
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
)
from app.services.suggestions.github import GithubApi

CHECK_LIMIT = 12
JOB_LIMIT = 3
BUILDS_PER_JOB = 5

_JOB_PATH = re.compile(r"^((?:/job/[^/?#]+)+)")
_FAILED_STATES = {"FAILURE", "ERROR"}
_FAILED_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "STARTUP_FAILURE"}
_FAILED_RESULTS = {"FAILURE": "failed", "UNSTABLE": "unstable"}

_CHECKS_QUERY = (
    "query($q: String!) { search(query: $q, type: ISSUE, first: 10) { nodes { ... on PullRequest { "
    "number title url repository { nameWithOwner } "
    "commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 40) { nodes { __typename "
    "... on StatusContext { context state targetUrl } "
    "... on CheckRun { name conclusion detailsUrl } } } } } } } } } } }"
)


def _failed_checks(data: dict) -> list[Item]:
    items: list[Item] = []
    search = data.get("search") if isinstance(data.get("search"), dict) else {}
    for pull in search.get("nodes") or []:
        if not isinstance(pull, dict) or not pull.get("url"):
            continue
        repo = str(((pull.get("repository") or {}) if isinstance(pull.get("repository"), dict) else {}).get("nameWithOwner") or "")
        where = f"{repo}#{pull.get('number')}"
        commits = pull.get("commits") if isinstance(pull.get("commits"), dict) else {}
        for node in commits.get("nodes") or []:
            commit = node.get("commit") if isinstance(node, dict) and isinstance(node.get("commit"), dict) else {}
            rollup = commit.get("statusCheckRollup") if isinstance(commit.get("statusCheckRollup"), dict) else {}
            contexts = rollup.get("contexts") if isinstance(rollup.get("contexts"), dict) else {}
            for check in contexts.get("nodes") or []:
                if not isinstance(check, dict):
                    continue
                if check.get("__typename") == "StatusContext":
                    failed = check.get("state") in _FAILED_STATES
                    name, url = check.get("context"), check.get("targetUrl")
                else:
                    failed = check.get("conclusion") in _FAILED_CONCLUSIONS
                    name, url = check.get("name"), check.get("detailsUrl")
                url = str(url or "").strip()
                if not failed or not url.lower().startswith(("http://", "https://")):
                    continue
                items.append(
                    Item(
                        value=url,
                        title=str(name or "check").strip(),
                        subtitle=f"{where} {str(pull.get('title') or '').strip()}".strip(),
                        status="failed",
                        url=url,
                        fields={"repo": repo, "pull_request": str(pull.get("url"))},
                    )
                )
                if len(items) >= CHECK_LIMIT:
                    return items
    return items


async def _pull_request_checks(ctx: SourceContext) -> Group | None:
    try:
        api = GithubApi(ctx)
    except SourceNotConfigured:
        return None
    identity = await api.identity()
    me = "@me" if identity.personal else identity.login
    if not me:
        return None
    data = await api.graphql(_CHECKS_QUERY, {"q": f"is:pr is:open author:{me} archived:false sort:updated-desc"})
    return Group("pr_checks", _failed_checks(data))


def _jenkins_instances(ctx: SourceContext) -> list[dict]:
    section = ctx.config.get("jenkins") if isinstance(ctx.config.get("jenkins"), dict) else {}
    if not section.get("enabled"):
        return []
    return [
        item
        for item in (section.get("instances") or [])
        if isinstance(item, dict) and item.get("enabled") is not False and str(item.get("url") or "").strip()
    ]


def _origin(parsed) -> tuple[str, str, int]:
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, (parsed.hostname or "").lower(), port


def jenkins_job_url(candidate: str, instances: list[dict]) -> tuple[dict, str] | None:
    """The job a Jenkins address belongs to, if the address is on one of the
    member's own Jenkins instances: ``(instance, job URL)``.

    ``.../job/folder/job/app/42/console`` names the job ``.../job/folder/job/app``.
    Anything that is not under a configured instance, or that tries to climb
    out of it, is refused.
    """
    try:
        parsed = urlparse((candidate or "").strip())
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    for instance in instances:
        base_url = str(instance.get("url") or "").strip().rstrip("/")
        base = urlparse(base_url)
        if _origin(parsed) != _origin(base):
            continue
        base_path = base.path.rstrip("/")
        if base_path and not parsed.path.startswith(base_path + "/"):
            continue
        match = _JOB_PATH.match(parsed.path[len(base_path):])
        if not match:
            continue
        job_path = match.group(1)
        lowered = job_path.lower()
        if "/../" in job_path + "/" or "/./" in job_path + "/" or "%2f" in lowered or "%5c" in lowered or "\\" in job_path:
            continue
        return instance, f"{base_url}{job_path}"
    return None


def _when(timestamp: Any) -> str:
    try:
        return datetime.fromtimestamp(int(timestamp) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


async def _job_builds(ctx: SourceContext, instance: dict, job_url: str) -> list[Item]:
    username = str(instance.get("username") or "").strip()
    secret = str(instance.get("token") or instance.get("password") or "").strip()
    headers = {"Accept": "application/json"}
    if username and secret:
        headers.update(connector_http.basic_auth_header(username, secret))
    response = await ctx.http.get(
        "jenkins",
        f"{job_url}/api/json",
        headers=headers,
        params={"tree": "fullName,builds[number,result,timestamp]{0,20}"},
        instance_setting=instance.get("proxy"),
    )
    data = json_of(response) if response.status_code == 200 else None
    if not isinstance(data, dict):
        return []
    name = str(data.get("fullName") or job_url.rsplit("/job/", 1)[-1]).strip()
    items: list[Item] = []
    for build in data.get("builds") or []:
        if not isinstance(build, dict) or not isinstance(build.get("number"), int):
            continue
        status = _FAILED_RESULTS.get(str(build.get("result") or ""))
        if not status:
            continue
        # Built from the validated job address, not from the address Jenkins
        # reports, which follows whatever root URL that server was given.
        url = f"{job_url}/{build['number']}/"
        items.append(
            Item(
                value=url,
                title=f"{name} #{build['number']}",
                meta=_when(build.get("timestamp")),
                status=status,
                url=url,
                fields={"job": name, "build": str(build["number"])},
            )
        )
        if len(items) >= BUILDS_PER_JOB:
            break
    return items


async def _jenkins_builds(ctx: SourceContext) -> Group | None:
    instances = _jenkins_instances(ctx)
    if not instances:
        return None
    jobs: list[tuple[dict, str]] = []
    for candidate in [ctx.q, *ctx.recent]:
        found = jenkins_job_url(candidate, instances)
        if found and found[1] not in [url for _instance, url in jobs]:
            jobs.append(found)
        if len(jobs) >= JOB_LIMIT:
            break
    if not jobs:
        return None
    items: list[Item] = []
    failure: SourceUnavailable | None = None
    for instance, job_url in jobs:
        try:
            items += await _job_builds(ctx, instance, job_url)
        except SourceUnavailable as exc:
            failure = exc
    if failure is not None and not items:
        raise failure
    return Group("jenkins", items)


async def failed_builds(ctx: SourceContext) -> Result:
    github_on = bool((ctx.config.get("github") or {}).get("enabled")) if isinstance(ctx.config.get("github"), dict) else False
    if not github_on and not _jenkins_instances(ctx):
        raise SourceNotConfigured("jenkins")
    groups = await gather_groups(_pull_request_checks(ctx), _jenkins_builds(ctx))
    return Result("ci.failed_builds", groups=groups)
