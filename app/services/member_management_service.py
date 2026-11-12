from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, distinct, func, select

from app.config import get_settings
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.agent_execution import AgentExecution
from app.models.agent_task import AgentTask
from app.models.delegation_rule import DelegationRule
from app.repositories.user_allowlist_repo import UserAllowlistRepository, normalize_username
from app.models.usage_daily_rollup import UsageDailyRollup
from app.repositories.user_repo import UserRepository
from app.services.usage_rollup_service import UsageRollupService, credit_value

# The periods the User Management panel offers for its consumption columns.
PERIOD_DAYS_OPTIONS = (7, 30, 90)
DEFAULT_PERIOD_DAYS = 30


def normalize_period_days(value: Any, *, default: int = DEFAULT_PERIOD_DAYS) -> int:
    """One of PERIOD_DAYS_OPTIONS; anything else is the default."""
    try:
        days = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return days if days in PERIOD_DAYS_OPTIONS else default


class MemberManagementService:
    """One row per member for Administration > User Management.

    All-time counts (assistants, tasks, delegations) come from the Portal's
    own tables; the consumption columns (executions, chats, credits, tokens,
    active days) are summed over the last ``days`` days from
    ``agent_executions``, which keeps what each run cost as the runtime
    reported it (app/services/agent_execution_registry.py). Nothing here
    needs an assistant to be running.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def _count_by(self, column, *filters) -> dict[int, int]:
        statement = select(column, func.count()).where(column.is_not(None), *filters).group_by(column)
        return {int(user_id): int(count) for user_id, count in self.db.execute(statement).all()}

    def _sum_by(self, column, value_column, *filters) -> dict[int, float]:
        statement = (
            select(column, func.coalesce(func.sum(value_column), 0))
            .where(column.is_not(None), value_column.is_not(None), *filters)
            .group_by(column)
        )
        return {int(user_id): float(total or 0) for user_id, total in self.db.execute(statement).all()}

    def _distinct_days_by(self, column, timestamp_column, *filters) -> dict[int, int]:
        statement = (
            select(column, func.count(distinct(func.date(timestamp_column))))
            .where(column.is_not(None), timestamp_column.is_not(None), *filters)
            .group_by(column)
        )
        return {int(user_id): int(count) for user_id, count in self.db.execute(statement).all()}

    def _latest_by(self, column, timestamp_column) -> dict[int, datetime]:
        statement = (
            select(column, func.max(timestamp_column))
            .where(column.is_not(None))
            .group_by(column)
        )
        return {
            int(user_id): timestamp
            for user_id, timestamp in self.db.execute(statement).all()
            if timestamp is not None
        }

    @staticmethod
    def _latest(*values: datetime | None) -> datetime | None:
        present = [value for value in values if value is not None]
        return max(present) if present else None

    def build_overview(
        self, *, days: int = DEFAULT_PERIOD_DAYS, now: datetime | None = None, group_id: int | None = None
    ) -> dict[str, Any]:
        """Every member's row, or only those of ``group_id`` (a group administrator's view)."""

        from app.repositories.group_repo import GroupRepository

        period_days = normalize_period_days(days)
        period_start = (now or datetime.utcnow()) - timedelta(days=period_days)
        in_period = AgentExecution.created_at >= period_start

        groups = GroupRepository(self.db).list_all()
        default_group_id = next((group.id for group in groups if group.is_default), None)
        group_names = {group.id: group.name for group in groups}

        def group_of(value: int | None) -> int | None:
            return value if value is not None else default_group_id

        all_users = UserRepository(self.db).list_all()
        users = all_users
        all_allowlist_entries = UserAllowlistRepository(self.db).list_all()
        allowlist_by_username = {
            normalize_username(entry.username): entry for entry in all_allowlist_entries
        }
        allowlist_entries = all_allowlist_entries
        if group_id is not None:
            users = [user for user in users if group_of(user.group_id) == group_id]
            allowlist_entries = [entry for entry in all_allowlist_entries if group_of(entry.group_id) == group_id]

        agent_counts = self._count_by(Agent.owner_user_id)
        task_counts = self._count_by(AgentTask.owner_user_id)
        completed_task_counts = self._count_by(AgentTask.owner_user_id, AgentTask.status == "done")
        execution_counts = self._count_by(AgentExecution.created_by_user_id)
        chat_counts = self._count_by(
            AgentExecution.created_by_user_id,
            AgentExecution.kind == "chat",
        )
        delegation_counts = self._count_by(DelegationRule.owner_user_id)
        period_execution_counts = self._count_by(AgentExecution.created_by_user_id, in_period)
        period_chat_counts = self._count_by(AgentExecution.created_by_user_id, AgentExecution.kind == "chat", in_period)
        # Copilot's own charge where the runtime reported one, the estimate otherwise.
        period_credits = self._sum_by(AgentExecution.created_by_user_id, credit_value(), in_period)
        period_input_tokens = self._sum_by(AgentExecution.created_by_user_id, AgentExecution.input_tokens, in_period)
        period_output_tokens = self._sum_by(AgentExecution.created_by_user_id, AgentExecution.output_tokens, in_period)
        period_active_days = self._distinct_days_by(AgentExecution.created_by_user_id, AgentExecution.created_at, in_period)
        latest_agent_activity = self._latest_by(Agent.owner_user_id, Agent.last_activity_at)
        latest_task_activity = self._latest_by(AgentTask.owner_user_id, AgentTask.updated_at)
        latest_execution_activity = self._latest_by(
            AgentExecution.created_by_user_id,
            AgentExecution.updated_at,
        )

        member_rows: list[dict[str, Any]] = []
        registered_usernames: set[str] = set()
        for user in users:
            normalized = normalize_username(user.username)
            registered_usernames.add(normalized)
            allowlist_entry = allowlist_by_username.get(normalized)
            member_rows.append(
                {
                    "id": user.id,
                    "username": user.username,
                    "nickname": user.nickname,
                    "role": user.role,
                    "group_id": group_of(user.group_id),
                    "group_name": group_names.get(group_of(user.group_id)),
                    "is_allowlisted": bool(allowlist_entry and allowlist_entry.is_active),
                    "allowlist_entry_id": allowlist_entry.id if allowlist_entry else None,
                    "created_at": user.created_at,
                    "last_login_at": user.last_login_at,
                    "last_activity_at": self._latest(
                        user.last_login_at,
                        latest_agent_activity.get(user.id),
                        latest_task_activity.get(user.id),
                        latest_execution_activity.get(user.id),
                    ),
                    "assistant_count": agent_counts.get(user.id, 0),
                    "task_count": task_counts.get(user.id, 0),
                    "completed_task_count": completed_task_counts.get(user.id, 0),
                    "execution_count": execution_counts.get(user.id, 0),
                    "chat_count": chat_counts.get(user.id, 0),
                    "delegation_count": delegation_counts.get(user.id, 0),
                    "execution_count_period": period_execution_counts.get(user.id, 0),
                    "chat_count_period": period_chat_counts.get(user.id, 0),
                    "credits_period": round(period_credits.get(user.id, 0.0), 2),
                    "input_tokens_period": int(period_input_tokens.get(user.id, 0)),
                    "output_tokens_period": int(period_output_tokens.get(user.id, 0)),
                    "active_days_period": period_active_days.get(user.id, 0),
                }
            )

        # An entry for an account in another group is not pending either.
        registered_usernames |= {normalize_username(user.username) for user in all_users}
        pending_entries = [
            entry for entry in allowlist_entries if normalize_username(entry.username) not in registered_usernames
        ]
        return {
            "users": member_rows,
            "allowlist_entries": allowlist_entries,
            "pending_allowlist_entries": pending_entries,
            "group_id": group_id,
            # A group administrator's overview names their own group only.
            "groups": [
                {"id": group.id, "name": group.name, "is_default": bool(group.is_default)}
                for group in groups
                if group_id is None or group.id == group_id
            ],
            "period_days": period_days,
            "summary": {
                "total_users": len(member_rows),
                "allowed_users": sum(1 for row in member_rows if row["is_allowlisted"]),
                "admin_users": sum(
                    1
                    for row in member_rows
                    if row["role"] in ("admin", "group_admin") and row["is_allowlisted"]
                ),
                "pending_invitations": len(pending_entries),
                "period_days": period_days,
                # The counts in User Management's Show filter, one per option:
                # each matches the rows that option shows (admin_users.js
                # applyMemberFilters), whatever the member's access.
                "blocked_users": sum(1 for row in member_rows if not row["is_allowlisted"]),
                "inactive_members_period": sum(1 for row in member_rows if row["execution_count_period"] == 0),
                "administrator_members": sum(1 for row in member_rows if row["role"] in ("admin", "group_admin")),
                "active_members_period": sum(1 for row in member_rows if row["execution_count_period"] > 0),
                "credits_period_total": round(sum(row["credits_period"] for row in member_rows), 2),
                "input_tokens_period_total": sum(row["input_tokens_period"] for row in member_rows),
                "output_tokens_period_total": sum(row["output_tokens_period"] for row in member_rows),
            },
        }

    def member_usage(self, user_id: int, *, days: int = DEFAULT_PERIOD_DAYS, now: datetime | None = None) -> dict[str, Any]:
        """One member's runs over the period, from the daily rollups.

        A daily credits series (zero days included, with bar heights as a
        percentage of the member's busiest day), the models and the kinds of
        run with their credits and failure rates, and the totals. Today and
        yesterday are rolled up first unless that happened in the last few
        minutes, so a run that just finished is counted.
        """
        period_days = normalize_period_days(days)
        today = (now or datetime.utcnow()).date()
        rollups = UsageRollupService(self.db)
        rollups.refresh_recent(today=today)
        start = rollups.period_start(period_days, today=today)
        R = UsageDailyRollup
        in_period = (R.user_id == int(user_id), R.day >= start, R.day <= today)

        day_rows = self.db.execute(
            select(R.day, func.coalesce(func.sum(R.credits), 0.0), func.coalesce(func.sum(R.executions), 0))
            .where(*in_period)
            .group_by(R.day)
        ).all()
        by_day = {day: (float(credits), int(executions)) for day, credits, executions in day_rows}
        peak = max((credits for credits, _executions in by_day.values()), default=0.0)
        daily = []
        for offset in range(period_days):
            day = start + timedelta(days=offset)
            credits, executions = by_day.get(day, (0.0, 0))
            daily.append(
                {
                    "day": day.isoformat(),
                    "credits": round(credits, 4),
                    "executions": executions,
                    "height": round(credits * 100 / peak, 1) if peak > 0 else 0,
                }
            )

        def _group(column, label):
            rows = self.db.execute(
                select(
                    column,
                    func.coalesce(func.sum(R.executions), 0),
                    func.coalesce(func.sum(R.failed), 0),
                    func.coalesce(func.sum(R.credits), 0.0),
                    func.coalesce(func.sum(R.input_tokens), 0),
                    func.coalesce(func.sum(R.output_tokens), 0),
                )
                .where(*in_period)
                .group_by(column)
                .order_by(func.sum(R.credits).desc(), func.sum(R.executions).desc())
                .limit(8)
            ).all()
            return [
                {
                    label: value or "",
                    "executions": int(executions),
                    "failed": int(failed),
                    "failure_rate": round(int(failed) / int(executions), 4) if int(executions) else None,
                    "credits": round(float(credits), 2),
                    "input_tokens": int(input_tokens),
                    "output_tokens": int(output_tokens),
                }
                for value, executions, failed, credits, input_tokens, output_tokens in rows
            ]

        by_model = _group(R.model, "model")
        by_kind = _group(R.kind, "kind")
        executions = sum(row["executions"] for row in by_kind)
        failed = sum(row["failed"] for row in by_kind)
        return {
            "user_id": int(user_id),
            "period_days": period_days,
            "period_start": start.isoformat(),
            "period_end": today.isoformat(),
            "totals": {
                "executions": executions,
                "failed": failed,
                "failure_rate": round(failed / executions, 4) if executions else None,
                "credits": round(sum(row["credits"] for row in by_kind), 2),
                "input_tokens": sum(row["input_tokens"] for row in by_kind),
                "output_tokens": sum(row["output_tokens"] for row in by_kind),
                "active_days": sum(1 for row in daily if row["executions"] > 0),
            },
            "daily": daily,
            "by_model": by_model,
            "by_kind": by_kind,
            "by_agent": self._usage_by_agent(user_id, start, today),
        }

    def member_work_counts(self, user_id: int) -> dict[str, int]:
        """The member's tasks (done / all) and delegation rules, all time: the member drawer's line under the name."""
        uid = int(user_id)
        tasks = self.db.execute(
            select(func.count(AgentTask.id), func.coalesce(func.sum(case((AgentTask.status == "done", 1), else_=0)), 0))
            .where(AgentTask.owner_user_id == uid)
        ).one()
        delegations = self.db.execute(
            select(func.count(DelegationRule.id)).where(DelegationRule.owner_user_id == uid)
        ).scalar_one()
        return {"task_count": int(tasks[0] or 0), "completed_task_count": int(tasks[1] or 0), "delegation_count": int(delegations or 0)}

    def member_month_to_date(self, user_id: int, *, now: datetime | None = None) -> dict[str, Any]:
        """The member's credits and runs since the first day of the current month, from the rollups."""
        moment = now or datetime.utcnow()
        today = moment.date()
        rollups = UsageRollupService(self.db)
        rollups.refresh_recent(today=today)
        month_start = today.replace(day=1)
        R = UsageDailyRollup
        row = self.db.execute(
            select(func.coalesce(func.sum(R.credits), 0.0), func.coalesce(func.sum(R.executions), 0))
            .where(R.user_id == int(user_id), R.day >= month_start, R.day <= today)
        ).one()
        return {"month": today.strftime("%Y-%m"), "credits": round(float(row[0]), 2), "executions": int(row[1])}

    def credit_notice(self, user_id: int, *, now: datetime | None = None) -> dict[str, Any]:
        """Month-to-date credits against the MEMBER_CREDIT_NOTICE_THRESHOLD guideline (0 = none)."""
        threshold = float(getattr(get_settings(), "member_credit_notice_threshold", 0) or 0)
        month = self.member_month_to_date(user_id, now=now)
        return {
            **month,
            "threshold": round(threshold, 2) if threshold > 0 else None,
            "over": bool(threshold > 0 and month["credits"] >= threshold),
            "ratio": round(month["credits"] / threshold, 4) if threshold > 0 else None,
        }

    def _usage_by_agent(self, user_id: int, start, today, limit: int = 8) -> list[dict[str, Any]]:
        R = UsageDailyRollup
        rows = self.db.execute(
            select(R.agent_id, Agent.name, func.coalesce(func.sum(R.executions), 0), func.coalesce(func.sum(R.credits), 0.0))
            .select_from(R)
            .outerjoin(Agent, Agent.id == R.agent_id)
            .where(R.user_id == int(user_id), R.day >= start, R.day <= today, R.agent_id != "")
            .group_by(R.agent_id, Agent.name)
            .order_by(func.sum(R.credits).desc(), func.sum(R.executions).desc())
            .limit(limit)
        ).all()
        return [
            {"agent_id": agent_id, "name": name or agent_id, "executions": int(executions), "credits": round(float(credits), 2)}
            for agent_id, name, executions, credits in rows
        ]
