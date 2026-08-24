from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.agent_execution import AgentExecution
from app.models.agent_task import AgentTask
from app.models.delegation_rule import DelegationRule
from app.repositories.user_allowlist_repo import UserAllowlistRepository, normalize_username
from app.repositories.user_repo import UserRepository


class MemberManagementService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _count_by(self, column, *filters) -> dict[int, int]:
        statement = select(column, func.count()).where(column.is_not(None), *filters).group_by(column)
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

    def build_overview(self) -> dict[str, Any]:
        users = UserRepository(self.db).list_all()
        allowlist_entries = UserAllowlistRepository(self.db).list_all()
        allowlist_by_username = {
            normalize_username(entry.username): entry for entry in allowlist_entries
        }

        agent_counts = self._count_by(Agent.owner_user_id)
        task_counts = self._count_by(AgentTask.owner_user_id)
        completed_task_counts = self._count_by(AgentTask.owner_user_id, AgentTask.status == "done")
        execution_counts = self._count_by(AgentExecution.created_by_user_id)
        chat_counts = self._count_by(
            AgentExecution.created_by_user_id,
            AgentExecution.kind == "chat",
        )
        delegation_counts = self._count_by(DelegationRule.owner_user_id)
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
                    "is_active": user.is_active,
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
                }
            )

        pending_entries = [
            entry for entry in allowlist_entries if normalize_username(entry.username) not in registered_usernames
        ]
        return {
            "users": member_rows,
            "allowlist_entries": allowlist_entries,
            "pending_allowlist_entries": pending_entries,
            "summary": {
                "total_users": len(member_rows),
                "active_users": sum(1 for row in member_rows if row["is_active"]),
                "allowed_users": sum(1 for row in member_rows if row["is_allowlisted"]),
                "admin_users": sum(
                    1
                    for row in member_rows
                    if row["role"] == "admin" and row["is_active"] and row["is_allowlisted"]
                ),
                "pending_invitations": len(pending_entries),
            },
        }
