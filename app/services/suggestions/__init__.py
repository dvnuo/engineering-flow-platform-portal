"""Picker sources: a member's recent work, listed for a starter card.

A starter card that needs a value used to ask for it in a text box, and the
member typed or pasted an issue key, a pull request link, a branch name. The
Portal already holds what it takes to offer those instead: it knows who the
member is and it holds their connector settings. A card names a source
(``input.source: jira.issues`` in the agents repository's portal/cards.yaml)
and the picker lists what that source returns.

A source is a plain function from a request context to a Result. It is
deterministic and involves no model: listing a member's branches costs one or
two API calls and no credits, where asking the assistant to "find the
candidates first" cost a model turn and several tool calls before the member
could answer anything.

Adding a source: write the function in a module here, register it below, give
its groups words in app/static/js/card_picker.js, and name it in the card.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from app.redaction import sanitize_exception_message
from app.services.suggestions import ci, confluence, github, jira, portal_sources
from app.services.suggestions.base import (
    SOURCE_TIMEOUT_SECONDS,
    STATE_NOT_CONFIGURED,
    STATE_OK,
    STATE_UNAVAILABLE,
    Result,
    SourceContext,
    SourceNotConfigured,
    SourceUnavailable,
)
from app.services.suggestions.cache import TTLCache

logger = logging.getLogger(__name__)

# Long enough that hovering a card, opening it and reopening it a moment later
# is one upstream call; short enough that a branch pushed a minute ago shows.
DEFAULT_TTL_SECONDS = 60.0
# A failure is remembered briefly, so a system that is down does not make
# every keystroke wait out the timeout again.
FAILURE_TTL_SECONDS = 10.0


@dataclass(frozen=True)
class SourceSpec:
    id: str
    handler: Callable[[SourceContext], Awaitable[Result]]
    # The connector a member would set up to make this source work.
    connector: str = ""
    # Whether typing asks the server again. The others return a short list
    # the page filters by itself.
    searchable: bool = True
    # Whether the answer depends on the selected assistant.
    uses_agent: bool = False
    ttl: float = DEFAULT_TTL_SECONDS


SOURCES: dict[str, SourceSpec] = {
    spec.id: spec
    for spec in (
        SourceSpec("jira.issues", jira.issues, connector="jira"),
        SourceSpec("jira.projects", jira.projects, connector="jira"),
        SourceSpec("github.pull_requests", github.pull_requests, connector="github"),
        SourceSpec("github.branches", github.branches, connector="github", uses_agent=True),
        SourceSpec("github.repos", github.repos, connector="github"),
        SourceSpec("github.paths", github.paths, connector="github"),
        SourceSpec("confluence.pages", confluence.pages, connector="confluence"),
        SourceSpec("ci.failed_builds", ci.failed_builds, connector="jenkins"),
        SourceSpec("mobile.stories", portal_sources.mobile_stories, connector="jira", uses_agent=True),
        # The Portal's own records change as runs finish; keep them fresh.
        SourceSpec("portal.mobile_runs", portal_sources.mobile_runs, searchable=False, uses_agent=True, ttl=5.0),
        SourceSpec("aws.accounts", portal_sources.aws_accounts, connector="aws", searchable=False, ttl=0.0),
    )
}

_results = TTLCache()


def clear_cache() -> None:
    from app.services.suggestions import github as github_module
    from app.services.suggestions import jira as jira_module
    from app.services.suggestions.identity import clear_identities

    _results.clear()
    clear_identities()
    github_module._trees.clear()
    jira_module._projects.clear()


def describe_sources() -> list[dict]:
    """What the page needs to know before it asks: which sources exist, so a
    card naming one this Portal lacks stays a plain text box."""
    return [
        {"id": spec.id, "connector": spec.connector, "searchable": spec.searchable, "uses_agent": spec.uses_agent}
        for spec in SOURCES.values()
    ]


def _cache_key(spec: SourceSpec, ctx: SourceContext) -> tuple:
    params = tuple(sorted((name, tuple(values)) for name, values in ctx.params.items()))
    agent_id = getattr(ctx.agent, "id", "") if spec.uses_agent else ""
    return (
        getattr(ctx.user, "id", None),
        ctx.revision,
        spec.id,
        agent_id,
        ctx.runtime_get is not None,
        ctx.q.strip().lower(),
        params,
        tuple(ctx.recent),
    )


def _ttl_of(spec: SourceSpec) -> Callable[[Result], float]:
    def ttl(result: Result) -> float:
        if result.state == STATE_OK:
            return spec.ttl
        if result.state == STATE_UNAVAILABLE:
            return FAILURE_TTL_SECONDS
        # Not configured costs nothing to work out, and the member is about
        # to go and configure it.
        return 0.0

    return ttl


async def suggest(source_id: str, ctx: SourceContext) -> Result:
    """Answer one picker request. Never raises for a known source: a system
    that is off, unreachable or slow is a state the page shows, with the text
    box still there."""
    spec = SOURCES[source_id]

    async def load() -> Result:
        try:
            result = await asyncio.wait_for(spec.handler(ctx), SOURCE_TIMEOUT_SECONDS)
        except SourceNotConfigured as exc:
            return Result(spec.id, STATE_NOT_CONFIGURED, connector=exc.connector or spec.connector)
        except SourceUnavailable as exc:
            return Result(spec.id, STATE_UNAVAILABLE, connector=spec.connector, reason=exc.reason)
        except asyncio.TimeoutError:
            return Result(spec.id, STATE_UNAVAILABLE, connector=spec.connector, reason="timeout")
        except Exception as exc:
            logger.warning(
                "Suggestion source %s failed: %s: %s", spec.id, type(exc).__name__, sanitize_exception_message(exc)
            )
            return Result(spec.id, STATE_UNAVAILABLE, connector=spec.connector, reason="error")
        result.source = spec.id
        result.connector = result.connector or spec.connector
        return result

    return await _results.get_or_load(_cache_key(spec, ctx), load, _ttl_of(spec))


__all__ = ["SOURCES", "SourceSpec", "clear_cache", "describe_sources", "suggest"]
