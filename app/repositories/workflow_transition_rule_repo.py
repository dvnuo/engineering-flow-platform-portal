from sqlalchemy import and_, desc, or_, select
from sqlalchemy.orm import Session

from app.models.workflow_transition_rule import WorkflowTransitionRule
from typing import Optional


class WorkflowTransitionRuleRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _normalize_system_type(system_type: str) -> str:
        return (system_type or "").strip().lower()

    def create(self, **kwargs) -> WorkflowTransitionRule:
        if "system_type" in kwargs:
            kwargs["system_type"] = self._normalize_system_type(kwargs["system_type"])
        rule = WorkflowTransitionRule(**kwargs)
        self.db.add(rule)
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def get_by_id(self, rule_id: str) -> Optional[WorkflowTransitionRule]:
        return self.db.get(WorkflowTransitionRule, rule_id)

    def list_all(self) -> list[WorkflowTransitionRule]:
        return list(self.db.scalars(select(WorkflowTransitionRule).order_by(WorkflowTransitionRule.created_at.desc())).all())

    def list_enabled_for_system(self, system_type: str) -> list[WorkflowTransitionRule]:
        normalized_system_type = self._normalize_system_type(system_type)
        stmt = (
            select(WorkflowTransitionRule)
            .where(
                and_(
                    WorkflowTransitionRule.system_type == normalized_system_type,
                    WorkflowTransitionRule.enabled.is_(True),
                )
            )
            .order_by(WorkflowTransitionRule.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def find_matching_jira_rule(
        self,
        project_key: str,
        issue_type: str,
        trigger_status: str,
        assignee_binding: str | None = None,
    ) -> Optional[WorkflowTransitionRule]:
        normalized_assignee = (assignee_binding or "").strip() or None

        stmt = (
            select(WorkflowTransitionRule)
            .where(
                and_(
                    WorkflowTransitionRule.system_type == "jira",
                    WorkflowTransitionRule.enabled.is_(True),
                    WorkflowTransitionRule.project_key == project_key,
                    WorkflowTransitionRule.issue_type == issue_type,
                    WorkflowTransitionRule.trigger_status == trigger_status,
                    or_(
                        WorkflowTransitionRule.assignee_binding.is_(None),
                        WorkflowTransitionRule.assignee_binding == normalized_assignee,
                    ),
                )
            )
            .order_by(desc(WorkflowTransitionRule.assignee_binding.is_not(None)), WorkflowTransitionRule.created_at.desc())
        )
        return self.db.scalars(stmt).first()

    def save(self, rule: WorkflowTransitionRule) -> WorkflowTransitionRule:
        self.db.add(rule)
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def delete(self, rule: WorkflowTransitionRule) -> None:
        self.db.delete(rule)
        self.db.commit()
