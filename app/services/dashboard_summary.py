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
ATTENTION_AGENT_STATUSES = {"failed"}


def _status_key(value: str | None) -> str:
    return (value or "unknown").strip().lower() or "unknown"


def _dt_sort_key(value: datetime | None) -> datetime:
    return value or datetime.min


class DashboardSummaryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build(self, user, *, scope: str = "all") -> dict[str, Any]:
        normalized_scope = "mine" if (scope or "").strip().lower() == "mine" else "all"
        generated_at = datetime.utcnow()
        window_start = generated_at - timedelta(hours=24)
        agents = self._visible_agents(user, normalized_scope)
        tasks = self._visible_tasks(user, normalized_scope)
        rules = self._visible_delegation_rules(user, normalized_scope)
        latest_runs = self._latest_delegation_runs(rules, limit=30)

        related_agent_by_id = self._agents_by_ids(
            {task.assignee_agent_id for task in tasks} | {rule.target_agent_id for rule in rules}
        )
        task_status_counts = Counter(_status_key(task.status) for task in tasks)
        agent_status_counts = Counter(_status_key(agent.status) for agent in agents)
        source_counts = Counter((rule.trigger_type or rule.source_type or "unknown") for rule in rules)

        active_tasks = [task for task in tasks if _status_key(task.status) in ACTIVE_TASK_STATUSES]
        attention_tasks = [task for task in tasks if _status_key(task.status) in ATTENTION_TASK_STATUSES]
        attention_agents = [
            agent for agent in agents
            if _status_key(agent.status) in ATTENTION_AGENT_STATUSES or bool((agent.last_error or "").strip())
        ]
        failed_runs = [run for run in latest_runs if _status_key(run.status) == "failed" or bool((run.error_message or "").strip())]
        due_rules = [rule for rule in rules if rule.enabled and rule.next_run_at and rule.next_run_at <= generated_at]
        missing_target_rules = [rule for rule in rules if rule.target_agent_id not in related_agent_by_id]

        tasks_by_agent: dict[str, list[AgentTask]] = defaultdict(list)
        for task in tasks:
            tasks_by_agent[task.assignee_agent_id].append(task)

        rules_by_agent: dict[str, list[DelegationRule]] = defaultdict(list)
        for rule in rules:
            rules_by_agent[rule.target_agent_id].append(rule)

        return {
            "scope": normalized_scope,
            "generated_at": generated_at,
            "agents": {
                "total": len(agents),
                "running": agent_status_counts.get("running", 0),
                "attention": len(attention_agents),
                "by_status": dict(sorted(agent_status_counts.items())),
            },
            "tasks": {
                "total": len(tasks),
                "active": len(active_tasks),
                "attention": len(attention_tasks),
                "done_24h": sum(1 for task in tasks if _status_key(task.status) == "done" and _dt_sort_key(task.updated_at) >= window_start),
                "failed_24h": sum(1 for task in tasks if _status_key(task.status) == "failed" and _dt_sort_key(task.updated_at) >= window_start),
                "by_status": dict(sorted(task_status_counts.items())),
            },
            "delegations": {
                "total": len(rules),
                "enabled": sum(1 for rule in rules if rule.enabled),
                "disabled": sum(1 for rule in rules if not rule.enabled),
                "due": len(due_rules),
                "failed_runs": len(failed_runs),
                "missing_targets": len(missing_target_rules),
                "by_source": dict(sorted(source_counts.items())),
            },
            "attention_items": self._attention_items(
                attention_tasks=attention_tasks,
                attention_agents=attention_agents,
                failed_runs=failed_runs,
                missing_target_rules=missing_target_rules,
                agent_by_id=related_agent_by_id,
            )[:8],
            "workload": self._workload_rows(
                agents=agents,
                tasks_by_agent=tasks_by_agent,
                rules_by_agent=rules_by_agent,
            )[:8],
            "delegation_health": self._delegation_health_rows(rules=rules, latest_runs=latest_runs, agent_by_id=related_agent_by_id)[:8],
            "recent_activity": self._recent_activity(tasks=tasks, latest_runs=latest_runs, rules=rules, agent_by_id=related_agent_by_id)[:10],
        }

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
        repo = DelegationRuleRepository(self.db)
        rules = repo.list(limit=5000)
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

    def _attention_items(
        self,
        *,
        attention_tasks: list[AgentTask],
        attention_agents: list[Agent],
        failed_runs: list[DelegationRuleRun],
        missing_target_rules: list[DelegationRule],
        agent_by_id: dict[str, Agent],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for task in attention_tasks:
            agent = agent_by_id.get(task.assignee_agent_id)
            items.append(
                {
                    "kind": "task",
                    "tone": "error" if _status_key(task.status) == "failed" else "warning",
                    "title": task.title or task.summary or task.task_type or task.id,
                    "meta": f"{_status_key(task.status)} on {(agent.name if agent else task.assignee_agent_id)}",
                    "timestamp": task.updated_at or task.created_at,
                    "target_id": task.id,
                    "target_type": "task",
                }
            )
        for agent in attention_agents:
            message = (agent.last_error or "").strip() or _status_key(agent.status)
            items.append(
                {
                    "kind": "agent",
                    "tone": "error",
                    "title": agent.name,
                    "meta": message,
                    "timestamp": agent.updated_at or agent.created_at,
                    "target_id": agent.id,
                    "target_type": "agent",
                }
            )
        for run in failed_runs:
            items.append(
                {
                    "kind": "delegation",
                    "tone": "error",
                    "title": "Delegation run failed",
                    "meta": run.error_message or f"Rule {run.rule_id}",
                    "timestamp": run.finished_at or run.started_at,
                    "target_id": run.rule_id,
                    "target_type": "delegation",
                }
            )
        for rule in missing_target_rules:
            items.append(
                {
                    "kind": "delegation",
                    "tone": "warning",
                    "title": rule.name,
                    "meta": "Target agent is missing or not visible",
                    "timestamp": rule.updated_at or rule.created_at,
                    "target_id": rule.id,
                    "target_type": "delegation",
                }
            )
        return sorted(items, key=lambda item: _dt_sort_key(item.get("timestamp")), reverse=True)

    def _workload_rows(
        self,
        *,
        agents: list[Agent],
        tasks_by_agent: dict[str, list[AgentTask]],
        rules_by_agent: dict[str, list[DelegationRule]],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for agent in agents:
            tasks = tasks_by_agent.get(agent.id, [])
            statuses = Counter(_status_key(task.status) for task in tasks)
            rows.append(
                {
                    "agent_id": agent.id,
                    "agent_name": agent.name,
                    "status": _status_key(agent.status),
                    "active_tasks": sum(statuses.get(status, 0) for status in ACTIVE_TASK_STATUSES),
                    "queued_tasks": statuses.get("queued", 0),
                    "running_tasks": statuses.get("running", 0),
                    "attention_tasks": sum(statuses.get(status, 0) for status in ATTENTION_TASK_STATUSES),
                    "delegations": len(rules_by_agent.get(agent.id, [])),
                    "updated_at": agent.updated_at or agent.created_at,
                }
            )
        return sorted(
            rows,
            key=lambda row: (row["attention_tasks"], row["active_tasks"], _dt_sort_key(row["updated_at"])),
            reverse=True,
        )

    def _delegation_health_rows(
        self,
        *,
        rules: list[DelegationRule],
        latest_runs: list[DelegationRuleRun],
        agent_by_id: dict[str, Agent],
    ) -> list[dict[str, Any]]:
        latest_run_by_rule: dict[str, DelegationRuleRun] = {}
        for run in latest_runs:
            latest_run_by_rule.setdefault(run.rule_id, run)
        rows = []
        for rule in rules:
            agent = agent_by_id.get(rule.target_agent_id)
            run = latest_run_by_rule.get(rule.id)
            rows.append(
                {
                    "rule_id": rule.id,
                    "name": rule.name,
                    "enabled": rule.enabled,
                    "source": rule.trigger_type or rule.source_type,
                    "agent_name": agent.name if agent else "Missing target",
                    "last_status": _status_key(run.status) if run else "never",
                    "last_run_at": (run.finished_at or run.started_at) if run else rule.last_run_at,
                    "next_run_at": rule.next_run_at,
                }
            )
        return sorted(rows, key=lambda row: _dt_sort_key(row.get("last_run_at") or row.get("next_run_at")), reverse=True)

    def _recent_activity(
        self,
        *,
        tasks: list[AgentTask],
        latest_runs: list[DelegationRuleRun],
        rules: list[DelegationRule],
        agent_by_id: dict[str, Agent],
    ) -> list[dict[str, Any]]:
        rule_by_id = {rule.id: rule for rule in rules}
        items: list[dict[str, Any]] = []
        for task in tasks[:20]:
            agent = agent_by_id.get(task.assignee_agent_id)
            items.append(
                {
                    "kind": "task",
                    "title": task.title or task.summary or task.task_type or task.id,
                    "meta": f"{_status_key(task.status)} on {(agent.name if agent else task.assignee_agent_id)}",
                    "timestamp": task.updated_at or task.created_at,
                    "target_id": task.id,
                    "target_type": "task",
                }
            )
        for run in latest_runs[:20]:
            rule = rule_by_id.get(run.rule_id)
            items.append(
                {
                    "kind": "delegation",
                    "title": rule.name if rule else "Delegation run",
                    "meta": _status_key(run.status),
                    "timestamp": run.finished_at or run.started_at,
                    "target_id": run.rule_id,
                    "target_type": "delegation",
                }
            )
        return sorted(items, key=lambda item: _dt_sort_key(item.get("timestamp")), reverse=True)[:10]
