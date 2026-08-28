from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.agent_task import AgentTask
from app.models.delegation_rule import DelegationRule, DelegationRuleRun
from app.repositories.delegation_rule_repo import DelegationRuleRepository


ACTIVE_TASK_STATUSES = {"queued", "running", "pending_restart"}
ATTENTION_TASK_STATUSES = {"failed", "blocked", "cancel_failed", "stale"}
SUCCESS_TASK_STATUSES = {"done", "success", "completed"}
WARNING_TASK_STATUSES = {"queued", "running", "pending_restart", "blocked", "stale"}
ERROR_TASK_STATUSES = {"failed", "cancel_failed"}
SUCCESS_RUN_STATUSES = {"success", "done", "completed"}
ERROR_RUN_STATUSES = {"failed", "error"}


def _status_key(value: str | None) -> str:
    return (value or "unknown").strip().lower() or "unknown"


def _dt_sort_key(value: datetime | None) -> datetime:
    return value or datetime.min


def _percent(part: int, total: int) -> int:
    if total <= 0:
        return 0
    return max(0, min(100, round((part / total) * 100)))


def _tone_for_task_status(status: str | None) -> str:
    key = _status_key(status)
    if key in SUCCESS_TASK_STATUSES:
        return "success"
    if key in ERROR_TASK_STATUSES:
        return "error"
    if key in WARNING_TASK_STATUSES:
        return "warning"
    return "neutral"


def _tone_for_run_status(status: str | None) -> str:
    key = _status_key(status)
    if key in SUCCESS_RUN_STATUSES:
        return "success"
    if key in ERROR_RUN_STATUSES:
        return "error"
    if key in {"running", "queued", "pending"}:
        return "warning"
    return "neutral"


def _tone_for_agent_status(status: str | None) -> str:
    key = _status_key(status)
    if key == "running":
        return "success"
    if key == "failed":
        return "error"
    if key in {"pending", "creating", "restarting", "stopped"}:
        return "warning"
    return "neutral"


def _health_summary(*, critical: int, warning: int, subject: str, total: int) -> dict[str, Any]:
    # An empty system is not a healthy system. Scoring 100/100 and "running
    # smoothly" with nothing to report reads as real data and hides the fact
    # that there is nothing here yet.
    if total <= 0:
        lowered = subject.lower()
        return {
            "score": None,
            "label": "No data",
            "tone": "neutral",
            "headline": f"No {lowered} yet",
            "critical": 0,
            "warning": 0,
            "empty": True,
        }

    score = max(0, min(100, 100 - critical * 14 - warning * 6))
    if critical > 0:
        label = "Needs attention"
        tone = "error"
        headline = f"Action is needed on {subject.lower()}"
    elif warning > 0:
        label = "Watch"
        tone = "warning"
        headline = f"A few {subject.lower()} need attention"
    else:
        label = "Healthy"
        tone = "success"
        headline = f"{subject} are running smoothly"
    return {
        "score": score,
        "label": label,
        "tone": tone,
        "headline": headline,
        "critical": critical,
        "warning": warning,
        "empty": False,
    }


class WorkOverviewService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build_tasks(self, user, *, scope: str = "all") -> dict[str, Any]:
        normalized_scope = self._normalize_scope(scope)
        generated_at = datetime.utcnow()
        window_start = generated_at - timedelta(hours=24)
        agents = self._visible_agents(user, normalized_scope)
        tasks = self._visible_tasks(user, normalized_scope)
        agent_by_id = self._agents_by_ids({task.assignee_agent_id for task in tasks})
        task_status_counts = Counter(_status_key(task.status) for task in tasks)
        active_tasks = [task for task in tasks if _status_key(task.status) in ACTIVE_TASK_STATUSES]
        attention_tasks = [task for task in tasks if _status_key(task.status) in ATTENTION_TASK_STATUSES]
        critical = sum(1 for task in attention_tasks if _status_key(task.status) in ERROR_TASK_STATUSES)
        warning = len(attention_tasks) - critical

        tasks_by_agent: dict[str, list[AgentTask]] = defaultdict(list)
        for task in tasks:
            tasks_by_agent[task.assignee_agent_id].append(task)

        return {
            "scope": normalized_scope,
            "generated_at": generated_at,
            "health": _health_summary(critical=critical, warning=warning, subject="Tasks", total=len(tasks)),
            "total": len(tasks),
            "active": len(active_tasks),
            "attention": len(attention_tasks),
            "done_24h": sum(
                1
                for task in tasks
                if _status_key(task.status) in SUCCESS_TASK_STATUSES
                and _dt_sort_key(task.updated_at) >= window_start
            ),
            "failed_24h": sum(
                1
                for task in tasks
                if _status_key(task.status) in ERROR_TASK_STATUSES
                and _dt_sort_key(task.updated_at) >= window_start
            ),
            "segments": self._task_segments(task_status_counts, total=len(tasks)),
            "priority_items": self._task_activity(attention_tasks, agent_by_id)[:8],
            "workload": self._workload_rows(agents=agents, tasks_by_agent=tasks_by_agent)[:8],
            "recent_activity": self._task_activity(tasks, agent_by_id)[:10],
        }

    def build_delegations(self, user, *, scope: str = "all") -> dict[str, Any]:
        normalized_scope = self._normalize_scope(scope)
        generated_at = datetime.utcnow()
        rules = self._visible_delegation_rules(user, normalized_scope)
        latest_runs = self._latest_delegation_runs(rules, limit=30)
        agent_by_id = self._agents_by_ids({rule.target_agent_id for rule in rules})
        source_counts = Counter((rule.trigger_type or rule.source_type or "unknown") for rule in rules)
        failed_runs = [
            run
            for run in latest_runs
            if _status_key(run.status) in ERROR_RUN_STATUSES or bool((run.error_message or "").strip())
        ]
        due_rules = [rule for rule in rules if rule.enabled and rule.next_run_at and rule.next_run_at <= generated_at]
        missing_target_rules = [rule for rule in rules if rule.target_agent_id not in agent_by_id]

        enabled = sum(1 for rule in rules if rule.enabled)
        disabled = len(rules) - enabled
        return {
            "scope": normalized_scope,
            "generated_at": generated_at,
            "health": _health_summary(
                critical=len(failed_runs),
                warning=len(due_rules) + len(missing_target_rules),
                subject="Delegations",
                total=len(rules),
            ),
            "total": len(rules),
            "enabled": enabled,
            "disabled": disabled,
            "due": len(due_rules),
            "failed_runs": len(failed_runs),
            "missing_targets": len(missing_target_rules),
            "by_source": dict(sorted(source_counts.items())),
            "segments": self._delegation_segments(
                enabled=enabled,
                disabled=disabled,
                due=len(due_rules),
                failed=len(failed_runs),
                total=len(rules),
            ),
            "priority_items": self._delegation_priority_items(
                failed_runs=failed_runs,
                due_rules=due_rules,
                missing_target_rules=missing_target_rules,
            )[:8],
            "health_rows": self._delegation_health_rows(
                rules=rules,
                latest_runs=latest_runs,
                agent_by_id=agent_by_id,
                now=generated_at,
            )[:8],
            "recent_activity": self._delegation_activity(latest_runs=latest_runs, rules=rules)[:10],
        }

    @staticmethod
    def _normalize_scope(scope: str) -> str:
        return "mine" if (scope or "").strip().lower() == "mine" else "all"

    def _visible_agents(self, user, scope: str) -> list[Agent]:
        stmt = select(Agent).order_by(Agent.updated_at.desc(), Agent.created_at.desc())
        if scope == "mine":
            stmt = stmt.where(Agent.owner_user_id == user.id)
        elif getattr(user, "role", "") != "admin":
            stmt = stmt.where(or_(Agent.owner_user_id == user.id, Agent.visibility == "public"))
        return list(self.db.scalars(stmt).all())

    def _visible_tasks(self, user, scope: str) -> list[AgentTask]:
        stmt = select(AgentTask).order_by(AgentTask.updated_at.desc(), AgentTask.created_at.desc())
        if scope == "mine":
            stmt = stmt.where(AgentTask.owner_user_id == user.id)
        return list(self.db.scalars(stmt).all())

    def _visible_delegation_rules(self, user, scope: str) -> list[DelegationRule]:
        rules = DelegationRuleRepository(self.db).list(limit=5000)
        if scope == "mine":
            rules = [rule for rule in rules if getattr(rule, "owner_user_id", None) == getattr(user, "id", None)]
        return rules

    def _agents_by_ids(self, agent_ids: set[str]) -> dict[str, Agent]:
        cleaned_ids = {agent_id for agent_id in agent_ids if agent_id}
        if not cleaned_ids:
            return {}
        rows = self.db.scalars(select(Agent).where(Agent.id.in_(cleaned_ids))).all()
        return {agent.id: agent for agent in rows}

    def _latest_delegation_runs(self, rules: list[DelegationRule], *, limit: int) -> list[DelegationRuleRun]:
        rule_ids = [rule.id for rule in rules]
        if not rule_ids:
            return []
        stmt = (
            select(DelegationRuleRun)
            .where(DelegationRuleRun.rule_id.in_(rule_ids))
            .order_by(DelegationRuleRun.started_at.desc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())

    @staticmethod
    def _task_segments(counts: Counter, *, total: int) -> list[dict[str, Any]]:
        return [
            {
                "status": status,
                "label": status.replace("_", " ").title(),
                "count": int(count),
                "percent": _percent(int(count), total),
                "tone": _tone_for_task_status(status),
            }
            for status, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]

    @staticmethod
    def _delegation_segments(
        *, enabled: int, disabled: int, due: int, failed: int, total: int
    ) -> list[dict[str, Any]]:
        segment_total = max(total, due + failed, enabled + disabled)
        items = [
            ("Enabled", enabled, "success"),
            ("Due", due, "warning"),
            ("Failed", failed, "error"),
            ("Disabled", disabled, "neutral"),
        ]
        return [
            {"label": label, "count": count, "percent": _percent(count, segment_total), "tone": tone}
            for label, count, tone in items
            if count > 0
        ]

    @staticmethod
    def _task_activity(tasks: list[AgentTask], agent_by_id: dict[str, Agent]) -> list[dict[str, Any]]:
        items = []
        for task in tasks:
            agent = agent_by_id.get(task.assignee_agent_id)
            items.append(
                {
                    "tone": _tone_for_task_status(task.status),
                    "title": task.title or task.summary or task.task_type or task.id,
                    "meta": f"{_status_key(task.status)} on {(agent.name if agent else task.assignee_agent_id)}",
                    "timestamp": task.updated_at or task.created_at,
                    "target_id": task.id,
                }
            )
        return sorted(items, key=lambda item: _dt_sort_key(item.get("timestamp")), reverse=True)

    @staticmethod
    def _workload_rows(
        *, agents: list[Agent], tasks_by_agent: dict[str, list[AgentTask]]
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for agent in agents:
            tasks = tasks_by_agent.get(agent.id, [])
            statuses = Counter(_status_key(task.status) for task in tasks)
            active_count = sum(statuses.get(status, 0) for status in ACTIVE_TASK_STATUSES)
            attention_count = sum(statuses.get(status, 0) for status in ATTENTION_TASK_STATUSES)
            rows.append(
                {
                    "agent_id": agent.id,
                    "agent_name": agent.name,
                    "status": _status_key(agent.status),
                    "status_tone": "error" if (agent.last_error or "").strip() else _tone_for_agent_status(agent.status),
                    "total_tasks": len(tasks),
                    "active_tasks": active_count,
                    "queued_tasks": statuses.get("queued", 0),
                    "running_tasks": statuses.get("running", 0),
                    "attention_tasks": attention_count,
                    "active_percent": _percent(active_count, max(1, len(tasks))),
                    "attention_percent": _percent(attention_count, max(1, len(tasks))),
                    "updated_at": agent.updated_at or agent.created_at,
                }
            )
        return sorted(
            rows,
            key=lambda row: (row["attention_tasks"], row["active_tasks"], _dt_sort_key(row["updated_at"])),
            reverse=True,
        )

    @staticmethod
    def _delegation_priority_items(
        *,
        failed_runs: list[DelegationRuleRun],
        due_rules: list[DelegationRule],
        missing_target_rules: list[DelegationRule],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for run in failed_runs:
            items.append(
                {
                    "tone": "error",
                    "title": "Delegation run failed",
                    "meta": run.error_message or f"Rule {run.rule_id}",
                    "timestamp": run.finished_at or run.started_at,
                    "target_id": run.rule_id,
                }
            )
        for rule in due_rules:
            items.append(
                {
                    "tone": "warning",
                    "title": rule.name,
                    "meta": "Scheduled run is due",
                    "timestamp": rule.next_run_at or rule.updated_at or rule.created_at,
                    "target_id": rule.id,
                }
            )
        for rule in missing_target_rules:
            items.append(
                {
                    "tone": "warning",
                    "title": rule.name,
                    "meta": "Target agent is missing or not visible",
                    "timestamp": rule.updated_at or rule.created_at,
                    "target_id": rule.id,
                }
            )
        return sorted(items, key=lambda item: _dt_sort_key(item.get("timestamp")), reverse=True)

    @staticmethod
    def _delegation_health_rows(
        *,
        rules: list[DelegationRule],
        latest_runs: list[DelegationRuleRun],
        agent_by_id: dict[str, Agent],
        now: datetime,
    ) -> list[dict[str, Any]]:
        latest_run_by_rule: dict[str, DelegationRuleRun] = {}
        for run in latest_runs:
            latest_run_by_rule.setdefault(run.rule_id, run)
        rows = []
        for rule in rules:
            agent = agent_by_id.get(rule.target_agent_id)
            run = latest_run_by_rule.get(rule.id)
            last_status_tone = _tone_for_run_status(run.status) if run else "neutral"
            is_due = bool(rule.enabled and rule.next_run_at and rule.next_run_at <= now)
            if last_status_tone == "error":
                row_tone = "error"
            elif not rule.enabled:
                row_tone = "neutral"
            elif is_due or agent is None:
                row_tone = "warning"
            else:
                row_tone = last_status_tone
            rows.append(
                {
                    "rule_id": rule.id,
                    "name": rule.name,
                    "enabled": rule.enabled,
                    "source": rule.trigger_type or rule.source_type,
                    "agent_name": agent.name if agent else "Missing target",
                    "last_status": _status_key(run.status) if run else "never",
                    "last_status_tone": last_status_tone,
                    "row_tone": row_tone,
                    "is_due": is_due,
                    "last_run_at": (run.finished_at or run.started_at) if run else rule.last_run_at,
                    "next_run_at": rule.next_run_at,
                }
            )
        return sorted(
            rows,
            key=lambda row: _dt_sort_key(row.get("last_run_at") or row.get("next_run_at")),
            reverse=True,
        )

    @staticmethod
    def _delegation_activity(
        *, latest_runs: list[DelegationRuleRun], rules: list[DelegationRule]
    ) -> list[dict[str, Any]]:
        rule_by_id = {rule.id: rule for rule in rules}
        items = []
        for run in latest_runs:
            rule = rule_by_id.get(run.rule_id)
            items.append(
                {
                    "tone": _tone_for_run_status(run.status),
                    "title": rule.name if rule else "Delegation run",
                    "meta": _status_key(run.status),
                    "timestamp": run.finished_at or run.started_at,
                    "target_id": run.rule_id,
                }
            )
        return sorted(items, key=lambda item: _dt_sort_key(item.get("timestamp")), reverse=True)
