"""Sources the Portal answers from what it already holds: its own task
records, the member's connector settings, and the selected assistant's
workspace. None of these leaves the cluster.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from sqlalchemy import or_

from app.models.agent_task import AgentTask
from app.services.efp_cards import MOBILE_RUN_SKILLS, scenario_progress
from app.services.suggestions import jira
from app.services.suggestions.base import (
    Group,
    Item,
    Result,
    SourceContext,
    SourceNotConfigured,
    SourceUnavailable,
)

RUN_LIMIT = 15
PLAN_LIMIT = 8
SCENARIOS_DIR = "mobile/scenarios"

_PLAN_DIR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")


def _iso(value: Any) -> str:
    try:
        return value.isoformat() if value is not None else ""
    except AttributeError:
        return str(value or "")


async def mobile_runs(ctx: SourceContext) -> Result:
    """The member's recent mobile scenario runs, the ones with failures first.

    A run is triaged from the files it left in an assistant's workspace, so
    with an assistant selected only that assistant's runs are offered.
    """
    names = sorted(MOBILE_RUN_SKILLS)
    query = ctx.db.query(AgentTask).filter(
        AgentTask.owner_user_id == getattr(ctx.user, "id", None),
        or_(AgentTask.skill_name.in_(names), AgentTask.skill_name.in_([f"/{name}" for name in names])),
    )
    if ctx.agent is not None:
        query = query.filter(AgentTask.assignee_agent_id == ctx.agent.id)
    tasks = query.order_by(AgentTask.updated_at.desc(), AgentTask.created_at.desc()).limit(RUN_LIMIT).all()

    needle = ctx.q.strip().lower()
    failed = Group("failed")
    recent = Group("recent")
    for task in tasks:
        title = str(task.title or "").strip() or "Mobile scenario run"
        if needle and needle not in f"{title} {task.id}".lower():
            continue
        progress = scenario_progress(task.result_payload_json) or {}
        failures = int(progress.get("failed") or 0)
        status = str(task.status or "").strip().lower()
        item = Item(
            value=str(task.id),
            title=title,
            subtitle=str(task.id),
            meta=_iso(task.updated_at or task.created_at),
            status="failed" if failures or status == "failed" else status,
            count=failures or None,
            url=f"/app/tasks/{task.id}",
            fields={"task_id": str(task.id)},
        )
        (failed if item.status == "failed" else recent).items.append(item)
    return Result("portal.mobile_runs", groups=[failed, recent])


async def _plan_item(ctx: SourceContext, name: str, modified_at: str) -> Item | None:
    status, data = await ctx.runtime_get(  # type: ignore[misc]
        "api/server-files/read", [("path", f"{SCENARIOS_DIR}/{name}/scenarios.json")]
    )
    if status != 200 or not isinstance(data, dict) or data.get("truncated"):
        return None
    try:
        plan = json.loads(str(data.get("content") or ""))
    except ValueError:
        return None
    if not isinstance(plan, dict):
        return None
    scenarios = plan.get("scenarios") if isinstance(plan.get("scenarios"), list) else []
    issue = str(plan.get("issue") or "").strip()
    key = issue if jira.issue_key_in(issue) else name
    return Item(
        value=key,
        title=key,
        subtitle=str(plan.get("title") or "").strip(),
        meta=str(plan.get("updated_at") or modified_at or "").strip(),
        status="scenarios",
        count=len(scenarios),
        url=str(plan.get("issue_url") or "").strip(),
        fields={"key": key, "plan": f"{SCENARIOS_DIR}/{name}/scenarios.json"},
    )


async def _planned_stories(ctx: SourceContext) -> Group | None:
    """Stories that already have a scenario plan in the selected assistant's
    workspace (mobile/scenarios/<KEY>/scenarios.json), newest first. These are
    the ones a member comes back to: to record, to generate scripts, to run."""
    if ctx.runtime_get is None:
        return None
    try:
        status, data = await ctx.runtime_get("api/server-files", [("path", SCENARIOS_DIR)])
    except Exception:
        return None
    entries = data.get("items") if status == 200 and isinstance(data, dict) else None
    dirs = [
        entry
        for entry in (entries if isinstance(entries, list) else [])
        if isinstance(entry, dict)
        and (entry.get("is_dir") or entry.get("type") == "directory")
        and _PLAN_DIR.match(str(entry.get("name") or ""))
    ]
    dirs.sort(key=lambda entry: str(entry.get("modified_at") or ""), reverse=True)
    loaded = await asyncio.gather(
        *(_plan_item(ctx, str(entry["name"]), str(entry.get("modified_at") or "")) for entry in dirs[:PLAN_LIMIT]),
        return_exceptions=True,
    )
    items = [item for item in loaded if isinstance(item, Item)]
    items.sort(key=lambda item: item.meta, reverse=True)
    needle = ctx.q.strip().lower()
    if needle:
        items = [item for item in items if needle in f"{item.title} {item.subtitle}".lower()]
    return Group("planned", items) if items else None


async def mobile_stories(ctx: SourceContext) -> Result:
    """Jira stories for the mobile testing cards: the ones planned in this
    workspace first, then the member's stories in Jira."""
    planned = await _planned_stories(ctx)
    try:
        from_jira = await jira.issues(ctx)
    except (SourceNotConfigured, SourceUnavailable):
        # The workspace alone still answers the common case (the story the
        # member is in the middle of); only with neither is this a failure.
        if planned is None:
            raise
        return Result("mobile.stories", groups=[planned])
    listed = {item.value for item in (planned.items if planned else [])}
    groups = [planned] if planned else []
    for group in from_jira.groups:
        group.items = [item for item in group.items if item.value not in listed]
        groups.append(group)
    return Result("mobile.stories", groups=groups, identity=from_jira.identity)


async def aws_accounts(ctx: SourceContext) -> Result:
    """The environments a member can name: the account matrix of their AWS
    connector. Read from the settings; nothing is asked of AWS."""
    section = ctx.config.get("aws") if isinstance(ctx.config.get("aws"), dict) else {}
    rows = [
        row
        for row in (section.get("accounts") or [])
        if isinstance(row, dict) and row.get("enabled") is not False and str(row.get("name") or "").strip()
    ]
    if not section.get("enabled") or not rows:
        raise SourceNotConfigured("aws")
    default = str(section.get("default_account") or "").strip().lower()
    needle = ctx.q.strip().lower()
    items: list[Item] = []
    for row in rows:
        name = str(row.get("name")).strip()
        account_id = str(row.get("account_id") or "").strip()
        regions = [str(region) for region in (row.get("regions") or []) if str(region).strip()]
        subtitle = ", ".join([part for part in (account_id, " ".join(regions)) if part])
        if needle and needle not in f"{name} {subtitle}".lower():
            continue
        fields = {"account": name}
        if account_id:
            fields["account_id"] = account_id
        if regions:
            fields["region"] = regions[0]
        items.append(Item(value=name, title=name, subtitle=subtitle, fields=fields))
    # The connector's default account is the one most members mean.
    items.sort(key=lambda item: 0 if item.value.lower() == default else 1)
    return Result("aws.accounts", groups=[Group("accounts", items)])
