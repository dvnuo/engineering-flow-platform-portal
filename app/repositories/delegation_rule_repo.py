from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.delegation_rule import DelegationRule, DelegationRuleEvent, DelegationRuleRun


class DelegationRuleRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def is_deleted_rule(rule: DelegationRule | None) -> bool:
        if not rule:
            return False
        try:
            state = json.loads(rule.state_json or "{}")
        except Exception:
            state = {}
        return bool(state.get("deleted"))

    def create(self, create_data: dict, current_user_id: int | None = None) -> DelegationRule:
        payload = dict(create_data)
        if current_user_id is not None:
            payload.setdefault("created_by_user_id", current_user_id)
            payload.setdefault("owner_user_id", current_user_id)
        rule = DelegationRule(**payload)
        self.db.add(rule)
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def get(self, rule_id: str) -> DelegationRule | None:
        return self.db.get(DelegationRule, rule_id)

    def list(self, limit: int = 100, offset: int = 0, enabled: bool | None = None) -> list[DelegationRule]:
        if limit <= 0:
            return []
        collected: list[DelegationRule] = []
        batch_offset = max(0, offset)
        batch_size = max(50, min(200, limit))
        while len(collected) < limit:
            stmt = select(DelegationRule).order_by(DelegationRule.created_at.desc()).offset(batch_offset).limit(batch_size)
            if enabled is not None:
                stmt = stmt.where(DelegationRule.enabled.is_(enabled))
            rows = list(self.db.scalars(stmt).all())
            if not rows:
                break
            for row in rows:
                if self.is_deleted_rule(row):
                    continue
                collected.append(row)
                if len(collected) >= limit:
                    break
            batch_offset += len(rows)
        return collected

    def list_enabled_for_trigger(self, *, source_type: str, trigger_type: str) -> list[DelegationRule]:
        stmt = (
            select(DelegationRule)
            .where(
                and_(
                    DelegationRule.enabled.is_(True),
                    DelegationRule.source_type == source_type,
                    DelegationRule.trigger_type == trigger_type,
                )
            )
            .order_by(DelegationRule.created_at.desc())
        )
        rows = list(self.db.scalars(stmt).all())
        return [row for row in rows if not self.is_deleted_rule(row)]

    def update(self, rule: DelegationRule, update_data: dict) -> DelegationRule:
        for key, value in update_data.items():
            setattr(rule, key, value)
        self.db.add(rule)
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def delete(self, rule: DelegationRule) -> None:
        self.db.delete(rule)
        self.db.commit()

    def list_due_rules(self, now: datetime, limit: int) -> list[DelegationRule]:
        if limit <= 0:
            return []
        collected: list[DelegationRule] = []
        batch_offset = 0
        batch_size = max(50, min(200, limit))
        while len(collected) < limit:
            stmt = (
                select(DelegationRule)
                .where(
                    and_(
                        DelegationRule.enabled.is_(True),
                        DelegationRule.next_run_at.is_not(None),
                        DelegationRule.next_run_at <= now,
                        or_(DelegationRule.locked_until.is_(None), DelegationRule.locked_until < now),
                    )
                )
                .order_by(DelegationRule.next_run_at.asc())
                .offset(batch_offset)
                .limit(batch_size)
            )
            rows = list(self.db.scalars(stmt).all())
            if not rows:
                break
            for row in rows:
                if self.is_deleted_rule(row):
                    continue
                collected.append(row)
                if len(collected) >= limit:
                    break
            batch_offset += len(rows)
        return collected

    def acquire_due_rule_lock(self, rule_id: str, now: datetime, lease_seconds: int) -> DelegationRule | None:
        stmt = (
            update(DelegationRule)
            .where(
                and_(
                    DelegationRule.id == rule_id,
                    DelegationRule.enabled.is_(True),
                    DelegationRule.next_run_at <= now,
                    or_(DelegationRule.locked_until.is_(None), DelegationRule.locked_until < now),
                )
            )
            .values(
                locked_until=now + timedelta(seconds=lease_seconds),
                updated_at=now,
            )
        )
        result = self.db.execute(stmt)
        if result.rowcount != 1:
            self.db.rollback()
            return None
        self.db.commit()
        rule = self.get(rule_id)
        if self.is_deleted_rule(rule):
            return None
        return rule

    def release_lock_and_schedule_next(self, rule: DelegationRule, *, now: datetime, next_run_at: datetime | None) -> DelegationRule:
        rule.last_run_at = now
        rule.next_run_at = next_run_at
        rule.locked_until = None
        self.db.add(rule)
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def create_run(self, *, rule_id: str, status: str = "running", started_at: datetime | None = None) -> DelegationRuleRun:
        run = DelegationRuleRun(rule_id=rule_id, status=status, started_at=started_at or datetime.utcnow())
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def finish_run(
        self,
        run: DelegationRuleRun,
        *,
        status: str,
        found_count: int,
        created_task_count: int,
        skipped_count: int,
        error_message: str | None = None,
        metrics: dict | None = None,
    ) -> DelegationRuleRun:
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
    ) -> tuple[DelegationRuleEvent, bool]:
        event = DelegationRuleEvent(
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
                select(DelegationRuleEvent).where(
                    and_(DelegationRuleEvent.rule_id == rule_id, DelegationRuleEvent.dedupe_key == dedupe_key)
                )
            ).first()
            if existing is None:
                raise
            return existing, False

    def get_or_create_event_by_dedupe(
        self,
        *,
        rule_id: str,
        dedupe_key: str,
        source_payload_json: str,
        normalized_payload_json: str,
        status: str = "discovered",
    ) -> tuple[DelegationRuleEvent, bool]:
        return self.create_event_or_get_existing(
            rule_id=rule_id,
            dedupe_key=dedupe_key,
            source_payload_json=source_payload_json,
            normalized_payload_json=normalized_payload_json,
            status=status,
        )

    def get_event(self, event_id: str) -> DelegationRuleEvent | None:
        return self.db.get(DelegationRuleEvent, event_id)

    def get_event_by_dedupe(self, *, rule_id: str, dedupe_key: str) -> DelegationRuleEvent | None:
        return self.db.scalars(
            select(DelegationRuleEvent).where(
                and_(DelegationRuleEvent.rule_id == rule_id, DelegationRuleEvent.dedupe_key == dedupe_key)
            )
        ).first()

    def create_event(
        self,
        *,
        rule_id: str,
        dedupe_key: str,
        source_payload_json: str,
        normalized_payload_json: str,
        status: str = "discovered",
    ) -> DelegationRuleEvent:
        event = DelegationRuleEvent(
            rule_id=rule_id,
            dedupe_key=dedupe_key,
            status=status,
            source_payload_json=source_payload_json,
            normalized_payload_json=normalized_payload_json,
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def update_event_status(
        self,
        event: DelegationRuleEvent,
        *,
        status: str,
        task_id: str | None = None,
        error_message: str | None = None,
    ) -> DelegationRuleEvent:
        now = datetime.utcnow()
        event.status = status
        event.task_id = task_id
        event.error_message = error_message
        event.updated_at = now
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def update_event_normalized_payload(self, event: DelegationRuleEvent, normalized_payload: dict) -> DelegationRuleEvent:
        event.normalized_payload_json = json.dumps(normalized_payload or {})
        event.updated_at = datetime.utcnow()
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def claim_event_for_task_creation(
        self,
        event_id: str,
        *,
        now: datetime | None = None,
        stale_after_seconds: int = 300,
    ) -> bool:
        claim_now = now or datetime.utcnow()
        stale_before = claim_now - timedelta(seconds=max(1, stale_after_seconds))
        stmt = (
            update(DelegationRuleEvent)
            .where(
                and_(
                    DelegationRuleEvent.id == event_id,
                    DelegationRuleEvent.task_id.is_(None),
                    or_(
                        DelegationRuleEvent.status.in_(("discovered", "failed")),
                        and_(
                            DelegationRuleEvent.status == "creating_task",
                            or_(
                                DelegationRuleEvent.updated_at.is_(None),
                                DelegationRuleEvent.updated_at < stale_before,
                            ),
                        ),
                    ),
                )
            )
            .values(status="creating_task", error_message=None, updated_at=claim_now)
        )
        result = self.db.execute(stmt)
        if result.rowcount != 1:
            self.db.rollback()
            return False
        self.db.commit()
        return True

    def list_runs(self, rule_id: str, limit: int) -> list[DelegationRuleRun]:
        stmt = (
            select(DelegationRuleRun)
            .where(DelegationRuleRun.rule_id == rule_id)
            .order_by(DelegationRuleRun.started_at.desc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())

    def list_events(self, rule_id: str, limit: int) -> list[DelegationRuleEvent]:
        stmt = (
            select(DelegationRuleEvent)
            .where(DelegationRuleEvent.rule_id == rule_id)
            .order_by(DelegationRuleEvent.created_at.desc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())

    def list_events_pending_reply(self, rule_id: str, limit: int) -> list[DelegationRuleEvent]:
        if limit <= 0:
            return []
        stmt = (
            select(DelegationRuleEvent)
            .where(
                and_(
                    DelegationRuleEvent.rule_id == rule_id,
                    DelegationRuleEvent.task_id.is_not(None),
                    DelegationRuleEvent.status.in_(("task_created", "task_done")),
                )
            )
            .order_by(DelegationRuleEvent.updated_at.asc(), DelegationRuleEvent.created_at.asc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())
