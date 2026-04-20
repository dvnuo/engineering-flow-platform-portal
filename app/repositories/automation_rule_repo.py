from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.automation_rule import AutomationRule, AutomationRuleEvent, AutomationRuleRun


class AutomationRuleRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, create_data: dict, current_user_id: int | None = None) -> AutomationRule:
        payload = dict(create_data)
        if current_user_id is not None:
            payload.setdefault("created_by_user_id", current_user_id)
            payload.setdefault("owner_user_id", current_user_id)
        rule = AutomationRule(**payload)
        self.db.add(rule)
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def get(self, rule_id: str) -> AutomationRule | None:
        return self.db.get(AutomationRule, rule_id)

    def list(self, limit: int = 100, offset: int = 0, enabled: bool | None = None) -> list[AutomationRule]:
        stmt = select(AutomationRule).order_by(AutomationRule.created_at.desc()).offset(offset).limit(limit)
        if enabled is not None:
            stmt = stmt.where(AutomationRule.enabled.is_(enabled))
        return list(self.db.scalars(stmt).all())

    def update(self, rule: AutomationRule, update_data: dict) -> AutomationRule:
        for key, value in update_data.items():
            setattr(rule, key, value)
        self.db.add(rule)
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def delete(self, rule: AutomationRule) -> None:
        self.db.delete(rule)
        self.db.commit()

    def list_due_rules(self, now: datetime, limit: int) -> list[AutomationRule]:
        stmt = (
            select(AutomationRule)
            .where(
                and_(
                    AutomationRule.enabled.is_(True),
                    AutomationRule.next_run_at.is_not(None),
                    AutomationRule.next_run_at <= now,
                    or_(AutomationRule.locked_until.is_(None), AutomationRule.locked_until < now),
                )
            )
            .order_by(AutomationRule.next_run_at.asc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())

    def acquire_due_rule_lock(self, rule_id: str, now: datetime, lease_seconds: int) -> AutomationRule | None:
        rule = self.get(rule_id)
        if not rule:
            return None
        if not rule.enabled or not rule.next_run_at or rule.next_run_at > now:
            return None
        if rule.locked_until and rule.locked_until >= now:
            return None
        rule.locked_until = now + timedelta(seconds=lease_seconds)
        self.db.add(rule)
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def release_lock_and_schedule_next(self, rule: AutomationRule, *, now: datetime, next_run_at: datetime | None) -> AutomationRule:
        rule.last_run_at = now
        rule.next_run_at = next_run_at
        rule.locked_until = None
        self.db.add(rule)
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def create_run(self, *, rule_id: str, status: str = "running", started_at: datetime | None = None) -> AutomationRuleRun:
        run = AutomationRuleRun(rule_id=rule_id, status=status, started_at=started_at or datetime.utcnow())
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def finish_run(
        self,
        run: AutomationRuleRun,
        *,
        status: str,
        found_count: int,
        created_task_count: int,
        skipped_count: int,
        error_message: str | None = None,
        metrics: dict | None = None,
    ) -> AutomationRuleRun:
        run.status = status
        run.found_count = found_count
        run.created_task_count = created_task_count
        run.skipped_count = skipped_count
        run.finished_at = datetime.utcnow()
        run.error_message = error_message
        run.metrics_json = json.dumps(metrics or {})
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def create_event_or_get_existing(
        self,
        *,
        rule_id: str,
        dedupe_key: str,
        source_payload_json: str,
        normalized_payload_json: str,
        status: str = "discovered",
    ) -> tuple[AutomationRuleEvent, bool]:
        event = AutomationRuleEvent(
            rule_id=rule_id,
            dedupe_key=dedupe_key,
            status=status,
            source_payload_json=source_payload_json,
            normalized_payload_json=normalized_payload_json,
        )
        self.db.add(event)
        try:
            self.db.commit()
            self.db.refresh(event)
            return event, True
        except IntegrityError:
            self.db.rollback()
            existing = self.db.scalars(
                select(AutomationRuleEvent).where(
                    and_(AutomationRuleEvent.rule_id == rule_id, AutomationRuleEvent.dedupe_key == dedupe_key)
                )
            ).first()
            return existing, False

    def update_event_status(
        self,
        event: AutomationRuleEvent,
        *,
        status: str,
        task_id: str | None = None,
        error_message: str | None = None,
    ) -> AutomationRuleEvent:
        event.status = status
        event.task_id = task_id
        event.error_message = error_message
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def list_runs(self, rule_id: str, limit: int) -> list[AutomationRuleRun]:
        stmt = (
            select(AutomationRuleRun)
            .where(AutomationRuleRun.rule_id == rule_id)
            .order_by(AutomationRuleRun.started_at.desc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())

    def list_events(self, rule_id: str, limit: int) -> list[AutomationRuleEvent]:
        stmt = (
            select(AutomationRuleEvent)
            .where(AutomationRuleEvent.rule_id == rule_id)
            .order_by(AutomationRuleEvent.created_at.desc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())
