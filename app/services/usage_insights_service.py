"""Quality and adoption figures for the administration Usage panel.

Consumption (credits, tokens, runs) comes from the daily rollups
(``usage_rollup_service``). This module answers the two other questions an
administrator asks over the same period, from the Portal's own tables:

* quality: how often runs fail, how long they take, how much task work is
  redone, how often a run stopped at its credits budget, how much of the
  prompt was served from cache;
* adoption: which skills tasks use, which delegation sources exist, which
  connectors members have enabled, how long a new member takes to their first
  chat, and how many of each month's joiners are still active.

Everything is read on request; the queries are small (group-bys and a capped
list of durations), so nothing is cached or precomputed.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.models.agent_execution import AgentExecution
from app.models.agent_task import AgentTask
from app.models.delegation_rule import DelegationRule
from app.models.user import User
from app.repositories.delegation_rule_repo import DelegationRuleRepository
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.services.usage_rollup_service import FAILED_EXECUTION_STATUSES, UsageRollupService

logger = logging.getLogger(__name__)

# Connector sections of the member settings whose ``enabled`` flag says the member set it up.
CONNECTOR_SECTIONS = (
    ("llm", "Model provider"),
    ("jira", "Jira"),
    ("confluence", "Confluence"),
    ("github", "GitHub"),
    ("jenkins", "Jenkins"),
    ("nexus", "Nexus"),
    ("aws", "AWS"),
    ("splunk", "Splunk"),
    ("pgsql", "PostgreSQL"),
    ("mobile-auto", "BrowserStack"),
    ("proxy", "Proxy"),
)
DURATION_SAMPLE_LIMIT = 20000
TOP_LIMIT = 10


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return round(ordered[index], 1)


def _ratio(part: int | float, whole: int | float) -> float | None:
    return round(part / whole, 4) if whole else None


class UsageInsightsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _period_start(days: int, today: date | None) -> datetime:
        start_day = UsageRollupService.period_start(days, today=today)
        return datetime(start_day.year, start_day.month, start_day.day)

    # -------------------------------------------------------------- quality

    def quality(self, days: int, *, today: date | None = None) -> dict[str, Any]:
        start = self._period_start(days, today)
        in_period = AgentExecution.created_at >= start
        E = AgentExecution
        failed_case = case((E.status.in_(FAILED_EXECUTION_STATUSES), 1), else_=0)

        status_rows = self.db.execute(select(E.status, func.count()).where(in_period).group_by(E.status)).all()
        by_status = {str(status or ""): int(count) for status, count in status_rows}
        total = sum(by_status.values())
        failed = sum(count for status, count in by_status.items() if status in FAILED_EXECUTION_STATUSES)

        def _breakdown(column, label: str) -> list[dict[str, Any]]:
            rows = self.db.execute(
                select(column, func.count(), func.sum(failed_case))
                .where(in_period)
                .group_by(column)
                .order_by(func.count().desc())
                .limit(TOP_LIMIT)
            ).all()
            return [
                {label: value or "", "executions": int(count), "failed": int(failed_count or 0), "failure_rate": _ratio(int(failed_count or 0), int(count))}
                for value, count, failed_count in rows
            ]

        durations: dict[str, list[float]] = defaultdict(list)
        duration_rows = self.db.execute(
            select(E.kind, E.started_at, E.finished_at)
            .where(in_period, E.status == "succeeded", E.started_at.is_not(None), E.finished_at.is_not(None))
            .order_by(E.created_at.desc())
            .limit(DURATION_SAMPLE_LIMIT)
        ).all()
        for kind, started_at, finished_at in duration_rows:
            if finished_at >= started_at:
                durations[str(kind or "")].append((finished_at - started_at).total_seconds())
        duration_summary = {
            kind: {"samples": len(values), "p50_seconds": _percentile(values, 0.5), "p90_seconds": _percentile(values, 0.9)}
            for kind, values in sorted(durations.items())
        }

        token_row = self.db.execute(
            select(func.coalesce(func.sum(E.input_tokens), 0), func.coalesce(func.sum(E.cached_input_tokens), 0)).where(in_period)
        ).one()
        budget_pauses = int(
            self.db.execute(select(func.count()).where(in_period, E.terminal_reason.is_not(None), E.terminal_reason.ilike("%budget%"))).scalar() or 0
        )

        tasks_total = int(self.db.execute(select(func.count()).where(AgentTask.created_at >= start)).scalar() or 0)
        reworked = int(
            self.db.execute(
                select(func.count()).where(
                    AgentTask.created_at >= start,
                    (AgentTask.retry_count > 0) | AgentTask.input_payload_json.like('%"followup_task"%'),
                )
            ).scalar()
            or 0
        )
        return {
            "executions": total,
            "failed": failed,
            "failure_rate": _ratio(failed, total),
            "by_status": by_status,
            "by_model": _breakdown(E.model, "model"),
            "by_runtime": _breakdown(E.runtime_type, "runtime_type"),
            "durations": duration_summary,
            "cache_hit_ratio": _ratio(int(token_row[1]), int(token_row[0])),
            "budget_pauses": budget_pauses,
            "tasks": tasks_total,
            "tasks_reworked": reworked,
            "rework_rate": _ratio(reworked, tasks_total),
        }

    # ------------------------------------------------------------- adoption

    def adoption(self, days: int, *, today: date | None = None) -> dict[str, Any]:
        start = self._period_start(days, today)
        now = datetime.utcnow()

        skill_rows = self.db.execute(
            select(AgentTask.skill_name, func.count(), func.sum(case((AgentTask.status == "done", 1), else_=0)))
            .where(AgentTask.created_at >= start)
            .group_by(AgentTask.skill_name)
            .order_by(func.count().desc())
            .limit(TOP_LIMIT)
        ).all()
        skills = [
            {"skill_name": skill or "", "tasks": int(count), "done": int(done or 0), "done_rate": _ratio(int(done or 0), int(count))}
            for skill, count, done in skill_rows
        ]

        rules = DelegationRuleRepository(self.db).list(limit=5000)
        source_totals: Counter = Counter()
        source_enabled: Counter = Counter()
        for rule in rules:
            source = str(rule.trigger_type or rule.source_type or "unknown")
            source_totals[source] += 1
            if rule.enabled:
                source_enabled[source] += 1
        spawned_rows = self.db.execute(
            select(AgentTask.trigger, func.count())
            .where(AgentTask.created_at >= start, AgentTask.source == "delegation")
            .group_by(AgentTask.trigger)
        ).all()
        spawned = {str(trigger or "unknown"): int(count) for trigger, count in spawned_rows}
        delegation_sources = [
            {"source": source, "rules": total, "enabled": source_enabled.get(source, 0), "tasks_started": spawned.get(source, 0)}
            for source, total in source_totals.most_common()
        ]

        connectors = self._connectors_enabled()

        users = self.db.execute(select(User.id, User.created_at, User.username)).all()
        first_chat_rows = self.db.execute(
            select(AgentExecution.created_by_user_id, func.min(AgentExecution.created_at))
            .where(AgentExecution.kind == "chat", AgentExecution.created_by_user_id.is_not(None))
            .group_by(AgentExecution.created_by_user_id)
        ).all()
        first_chat = {int(user_id): first for user_id, first in first_chat_rows if first is not None}
        days_to_first_chat = [
            max(0.0, (first_chat[user_id] - created_at).total_seconds() / 86400)
            for user_id, created_at, _username in users
            if user_id in first_chat and created_at is not None
        ]
        active_rows = self.db.execute(
            select(func.distinct(AgentExecution.created_by_user_id)).where(AgentExecution.created_at >= now - timedelta(days=30))
        ).all()
        active_30 = {int(row[0]) for row in active_rows if row[0] is not None}
        cohorts: dict[str, dict[str, int]] = {}
        for user_id, created_at, _username in users:
            if created_at is None:
                continue
            month = created_at.strftime("%Y-%m")
            entry = cohorts.setdefault(month, {"joined": 0, "chatted": 0, "active_last_30_days": 0})
            entry["joined"] += 1
            if user_id in first_chat:
                entry["chatted"] += 1
            if user_id in active_30:
                entry["active_last_30_days"] += 1
        cohort_rows = [{"month": month, **values} for month, values in sorted(cohorts.items(), reverse=True)[:6]]

        return {
            "skills": skills,
            "delegation_sources": delegation_sources,
            "connectors": connectors,
            "members": len(users),
            "members_with_a_chat": len(first_chat),
            "days_to_first_chat_p50": _percentile(days_to_first_chat, 0.5),
            "members_active_last_30_days": len(active_30),
            "cohorts": cohort_rows,
        }

    def _connectors_enabled(self) -> list[dict[str, Any]]:
        """How many members have each connector enabled, as the assistants would see it (system or custom mode)."""
        from app.services.runtime_profile_service import RuntimeProfileService

        counts: Counter = Counter()
        profiles = RuntimeProfileRepository(self.db).list_all()
        service = RuntimeProfileService(self.db)
        for profile in profiles:
            try:
                config = service.effective_config_for(profile)
            except Exception:  # noqa: BLE001 - one unreadable profile must not blank the panel
                logger.debug("usage insights: could not read profile %s", getattr(profile, "id", "?"), exc_info=True)
                try:
                    config = json.loads(profile.config_json or "{}")
                except (TypeError, ValueError):
                    config = {}
            if not isinstance(config, dict):
                continue
            for section, _label in CONNECTOR_SECTIONS:
                block = config.get(section)
                if not isinstance(block, dict):
                    continue
                enabled = block.get("enabled")
                if section == "llm":
                    enabled = bool(block.get("api_key") or block.get("model")) if enabled is None else bool(enabled)
                if enabled:
                    counts[section] += 1
        return [
            {"connector": section, "label": label, "members": counts.get(section, 0), "profiles": len(profiles)}
            for section, label in CONNECTOR_SECTIONS
        ]
