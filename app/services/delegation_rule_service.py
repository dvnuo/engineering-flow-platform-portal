from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.delegation_rule_repo import DelegationRuleRepository
from app.schemas.delegation_rule import DelegationRuleCreate, DelegationRuleUpdate
from app.services.delegation_reply_service import DelegationReplyService, delegation_reply_marker
from app.services.delegation_source_config import (
    delegation_source_item_matches,
    normalize_delegation_source_conditions,
    normalize_delegation_source_scope,
)
from app.services.delegation_source_pollers import SOURCE_PROVIDER, SUPPORTED_DELEGATION_SOURCES, DelegationSourcePoller
from app.services.provider_config_resolver import (
    ProviderConfigResolverError,
    resolve_github_for_agent,
    resolve_jira_for_agent,
)
from app.services.task_dispatcher import TaskDispatcherService


AGENT_ASYNC_TASK_TYPE = "agent_async_task"
AGENT_ASYNC_TASK_FAMILY = "agent_task"
AGENT_ASYNC_TASK_AUTONOMOUS_INSTRUCTION = (
    "Run as a background long-running task. Do not ask the user for more information unless truly blocked. "
    "Make reasonable assumptions and complete as much as possible."
)
logger = logging.getLogger(__name__)


@dataclass
class RunOnceResult:
    rule_id: str
    status: str
    found_count: int
    created_task_count: int
    skipped_count: int
    run_id: str
    created_task_ids: list[str]


class DelegationRuleService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = DelegationRuleRepository(db)
        self.task_repo = AgentTaskRepository(db)
        self.dispatcher = TaskDispatcherService()
        self.source_poller = DelegationSourcePoller()
        self.reply_service = DelegationReplyService()

    @staticmethod
    def _parse_json(raw: str | None) -> dict:
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _json(data: dict | None) -> str:
        return json.dumps(data or {})

    @staticmethod
    def _validate_source(source: str | None) -> str:
        normalized = str(source or "").strip()
        if normalized not in SUPPORTED_DELEGATION_SOURCES:
            expected = ", ".join(sorted(SUPPORTED_DELEGATION_SOURCES))
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"source must be one of: {expected}")
        return normalized

    @staticmethod
    def _provider_for_source(source: str) -> str:
        return SOURCE_PROVIDER[source]

    @staticmethod
    def _agent_task_dedupe_key(full_dedupe_key: str) -> str:
        if len(full_dedupe_key) <= 240:
            return full_dedupe_key
        return "delegation:" + hashlib.sha256(full_dedupe_key.encode()).hexdigest()

    @staticmethod
    def _derive_task_title(task_content: str) -> str:
        first_line = next((line.strip() for line in str(task_content or "").splitlines() if line.strip()), "")
        title = " ".join((first_line or "Delegation task").split())
        if len(title) > 96:
            return title[:93].rstrip() + "..."
        return title

    def _validate_agent_provider_config(self, *, agent_id: str, provider: str, source_scope: dict | None = None) -> None:
        agent = AgentRepository(self.db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Target agent not found")
        try:
            if provider == "github":
                resolve_github_for_agent(self.db, agent.id)
            elif provider == "jira":
                resolve_jira_for_agent(self.db, agent.id, source_scope=source_scope)
            else:
                raise ProviderConfigResolverError(f"Unsupported provider: {provider}")
        except ProviderConfigResolverError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    def create_rule(self, payload: DelegationRuleCreate, current_user_id: int) -> object:
        source = self._validate_source(payload.source)
        provider = self._provider_for_source(source)
        source_scope = normalize_delegation_source_scope(source, payload.source_scope)
        source_conditions = normalize_delegation_source_conditions(source, payload.source_conditions)
        self._validate_agent_provider_config(agent_id=payload.target_agent_id, provider=provider, source_scope=source_scope)
        skill_name = str(payload.skill_name or "").strip()
        if not skill_name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="skill_name is required")
        interval_seconds = int(payload.interval_seconds or 60)
        now = datetime.utcnow()
        create_data = {
            "name": payload.name.strip(),
            "enabled": bool(payload.enabled),
            "source_type": provider,
            "trigger_type": source,
            "target_agent_id": payload.target_agent_id.strip(),
            "task_type": AGENT_ASYNC_TASK_TYPE,
            "scope_json": self._json(source_scope),
            "trigger_config_json": self._json(source_conditions),
            "task_config_json": self._json({"skill_name": skill_name}),
            "schedule_json": self._json({"interval_seconds": interval_seconds}),
            "state_json": "{}",
            "owner_user_id": current_user_id,
            "created_by_user_id": current_user_id,
            "next_run_at": now + timedelta(seconds=interval_seconds) if payload.enabled else None,
        }
        return self.repo.create(create_data, current_user_id=current_user_id)

    def update_rule(self, rule, payload: DelegationRuleUpdate, current_user_id: int) -> object:
        _ = current_user_id
        data = payload.model_dump(exclude_unset=True)
        source = self._validate_source(data.get("source") or rule.trigger_type)
        provider = self._provider_for_source(source)
        source_changed = "source" in data and source != rule.trigger_type
        if "source_scope" in data:
            source_scope = normalize_delegation_source_scope(source, data.get("source_scope") or {})
        elif source_changed:
            source_scope = {}
        else:
            source_scope = normalize_delegation_source_scope(source, self._parse_json(rule.scope_json))
        if "source_conditions" in data:
            source_conditions = normalize_delegation_source_conditions(source, data.get("source_conditions") or {})
        elif source_changed:
            source_conditions = {}
        else:
            source_conditions = normalize_delegation_source_conditions(source, self._parse_json(rule.trigger_config_json))
        target_agent_id = str(data.get("target_agent_id") or rule.target_agent_id).strip()
        should_validate_target = (
            "target_agent_id" in data
            or "source" in data
            or "source_scope" in data
            or ("enabled" in data and bool(data["enabled"]))
        )
        if should_validate_target:
            self._validate_agent_provider_config(agent_id=target_agent_id, provider=provider, source_scope=source_scope)

        task_config = self._parse_json(rule.task_config_json)
        schedule = self._parse_json(rule.schedule_json)
        update_data: dict = {}
        if "name" in data:
            update_data["name"] = data["name"].strip()
        if "source" in data:
            update_data["source_type"] = provider
            update_data["trigger_type"] = source
        if "source_scope" in data or source_changed:
            update_data["scope_json"] = self._json(source_scope)
        if "source_conditions" in data or source_changed:
            update_data["trigger_config_json"] = self._json(source_conditions)
        if "enabled" in data:
            update_data["enabled"] = bool(data["enabled"])
        if "target_agent_id" in data:
            update_data["target_agent_id"] = target_agent_id
        if "skill_name" in data:
            skill_name = str(data["skill_name"] or "").strip()
            if not skill_name:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="skill_name is required")
            task_config["skill_name"] = skill_name
            update_data["task_config_json"] = self._json(task_config)
        if "interval_seconds" in data:
            interval = int(data["interval_seconds"] or 60)
            schedule["interval_seconds"] = interval
            update_data["schedule_json"] = self._json(schedule)

        now = datetime.utcnow()
        enabled = bool(update_data.get("enabled", rule.enabled))
        interval_seconds = int(schedule.get("interval_seconds") or 60)
        if "enabled" in update_data and not enabled:
            update_data["next_run_at"] = None
            update_data["locked_until"] = None
        elif ("enabled" in update_data or "interval_seconds" in data) and enabled:
            update_data["next_run_at"] = now + timedelta(seconds=interval_seconds)
        update_data["task_type"] = AGENT_ASYNC_TASK_TYPE
        update_data["updated_at"] = now
        return self.repo.update(rule, update_data)

    @staticmethod
    def _source_payload_for_event(item: dict) -> dict:
        source_payload = item.get("source_payload")
        return source_payload if isinstance(source_payload, dict) else {}

    @staticmethod
    def _reaction_target_for_task(item: dict) -> dict:
        reaction_target = item.get("reaction_target")
        return reaction_target if isinstance(reaction_target, dict) else {}

    @staticmethod
    def _reply_target_for_task(item: dict) -> dict:
        reply_target = item.get("reply_target")
        return reply_target if isinstance(reply_target, dict) else {}

    @staticmethod
    def _portal_start_reaction_error(exc: Exception, reaction_target: dict) -> dict[str, Any]:
        return {
            "type": exc.__class__.__name__,
            "message": str(exc),
            "api_path": str((reaction_target or {}).get("api_path") or ""),
            "target": reaction_target or {},
        }

    async def _record_portal_start_reaction(self, *, rule, event, normalized_item: dict) -> dict:
        reaction_target = self._reaction_target_for_task(normalized_item)
        if not reaction_target:
            return normalized_item
        if str(reaction_target.get("provider") or "github").strip().lower() != "github":
            return normalized_item

        updated_item = dict(normalized_item)
        try:
            start_reaction = await self.reply_service.add_github_reaction(
                self.db,
                rule=rule,
                reaction_target=reaction_target,
                content="eyes",
            )
            updated_item["portal_start_reaction"] = start_reaction
            updated_item.pop("portal_start_reaction_error", None)
        except Exception as exc:
            error_payload = self._portal_start_reaction_error(exc, reaction_target)
            updated_item["portal_start_reaction_error"] = error_payload
            logger.warning(
                "Failed to add portal start reaction for delegation event %s rule %s target=%s error=%s",
                getattr(event, "id", "-"),
                getattr(rule, "id", "-"),
                reaction_target,
                error_payload["message"],
                exc_info=True,
            )

        self.repo.update_event_normalized_payload(event, updated_item)
        return updated_item

    @staticmethod
    def _portal_start_reply_error(exc: Exception, reply_target: dict) -> dict[str, Any]:
        return {
            "type": exc.__class__.__name__,
            "message": str(exc),
            "provider": str((reply_target or {}).get("provider") or ""),
            "issue_key": str((reply_target or {}).get("issue_key") or ""),
            "target": reply_target or {},
        }

    async def _record_portal_start_reply(self, *, rule, event, normalized_item: dict) -> dict:
        reply_target = self._reply_target_for_task(normalized_item)
        if not reply_target:
            return normalized_item
        provider = str(reply_target.get("provider") or normalized_item.get("provider") or rule.source_type or "").strip().lower()
        if provider != "jira":
            return normalized_item
        if str(reply_target.get("kind") or "").strip() != "issue_comment":
            return normalized_item
        if str(reply_target.get("reply_mode") or "").strip() == "update_comment" and str(reply_target.get("comment_id") or "").strip():
            return normalized_item

        updated_item = dict(normalized_item)
        try:
            start_reply = await self.reply_service.add_jira_start_comment(
                self.db,
                rule,
                reply_target,
                source=str(normalized_item.get("source") or rule.trigger_type or ""),
                source_url=normalized_item.get("source_url"),
                source_comment=normalized_item.get("source_comment"),
                event=event,
            )
            updated_reply_target = dict(reply_target)
            updated_reply_target["reply_mode"] = "update_comment"
            updated_reply_target["comment_id"] = str(start_reply["comment_id"])
            updated_item["reply_target"] = updated_reply_target
            updated_item["portal_start_reply"] = start_reply
            updated_item.pop("portal_start_reply_error", None)
        except Exception as exc:
            error_payload = self._portal_start_reply_error(exc, reply_target)
            updated_item["portal_start_reply_error"] = error_payload
            logger.warning(
                "Failed to add portal Jira start comment for delegation event %s rule %s target=%s error=%s",
                getattr(event, "id", "-"),
                getattr(rule, "id", "-"),
                reply_target,
                error_payload["message"],
                exc_info=True,
            )

        self.repo.update_event_normalized_payload(event, updated_item)
        return updated_item

    async def _record_portal_start_feedback(self, *, rule, event, normalized_item: dict) -> dict:
        updated_item = await self._record_portal_start_reaction(
            rule=rule,
            event=event,
            normalized_item=normalized_item,
        )
        return await self._record_portal_start_reply(
            rule=rule,
            event=event,
            normalized_item=updated_item,
        )

    async def _cleanup_portal_start_reaction(self, *, rule, event, normalized: dict) -> None:
        start_reaction = normalized.get("portal_start_reaction") if isinstance(normalized, dict) else None
        if not isinstance(start_reaction, dict) or not start_reaction:
            return

        cleanup_payload: dict[str, Any]
        updated = dict(normalized)
        try:
            cleanup_payload = await self.reply_service.delete_github_reaction(
                self.db,
                rule=rule,
                portal_start_reaction=start_reaction,
            )
            updated["portal_start_reaction_cleanup"] = cleanup_payload
            updated.pop("portal_start_reaction_cleanup_error", None)
        except Exception as exc:
            cleanup_payload = {
                "type": exc.__class__.__name__,
                "message": str(exc),
                "cleanup_api_path": str(start_reaction.get("cleanup_api_path") or ""),
            }
            updated["portal_start_reaction_cleanup_error"] = cleanup_payload
            logger.warning(
                "Failed to clean up portal start reaction for delegation event %s rule %s error=%s",
                getattr(event, "id", "-"),
                getattr(rule, "id", "-"),
                cleanup_payload["message"],
                exc_info=True,
            )

        try:
            self.repo.update_event_normalized_payload(event, updated)
        except Exception:
            logger.warning(
                "Failed to persist portal start reaction cleanup state for delegation event %s",
                getattr(event, "id", "-"),
                exc_info=True,
            )

    async def _create_task_for_source_item(self, *, rule, item: dict) -> tuple[object | None, bool]:
        source = self._validate_source(item.get("source") or rule.trigger_type)
        provider = self._provider_for_source(source)
        dedupe_key = str(item.get("dedupe_key") or "").strip()
        if not dedupe_key:
            raise ValueError("Source item is missing dedupe_key")
        task_content = str(item.get("task_content") or "").strip()
        if not task_content:
            raise ValueError("Source item is missing task_content")

        normalized_item = dict(item)
        normalized_item.setdefault("source", source)
        normalized_item.setdefault("provider", provider)
        agent_task_dedupe_key = self._agent_task_dedupe_key(f"{rule.id}:{dedupe_key}")
        event, _created = self.repo.get_or_create_event_by_dedupe(
            rule_id=rule.id,
            dedupe_key=dedupe_key,
            source_payload_json=self._json(self._source_payload_for_event(normalized_item)),
            normalized_payload_json=json.dumps(normalized_item),
            status="discovered",
        )
        event_status = (event.status or "").strip().lower()
        if event.task_id or event_status in {"task_created", "reply_sent", "reply_failed"}:
            return None, True
        if event_status not in {"discovered", "failed", "creating_task"}:
            return None, True
        if not self.repo.claim_event_for_task_creation(event.id):
            return None, True

        refreshed_event = self.repo.get_event(event.id) or event
        existing_task = self.task_repo.find_by_dedupe_key(
            assignee_agent_id=rule.target_agent_id,
            source="delegation",
            task_type=AGENT_ASYNC_TASK_TYPE,
            dedupe_key=agent_task_dedupe_key,
        )
        if existing_task:
            self.repo.update_event_status(refreshed_event, status="task_created", task_id=existing_task.id, error_message=None)
            return None, True

        normalized_item = await self._record_portal_start_feedback(
            rule=rule,
            event=refreshed_event,
            normalized_item=normalized_item,
        )
        task_config = self._parse_json(rule.task_config_json)
        skill_name = str(task_config.get("skill_name") or "").strip()
        if not skill_name:
            raise ValueError("Delegation rule is missing skill_name")
        task_id = str(uuid4())
        task_session_id = f"agent-task:{task_id}"
        delegation_payload = {
            "delegation_rule_id": rule.id,
            "source": source,
            "provider": provider,
            "source_url": normalized_item.get("source_url"),
            "source_comment": normalized_item.get("source_comment"),
            "represented_identity": normalized_item.get("represented_identity"),
            "reply_target": normalized_item.get("reply_target") or {},
            "source_payload": self._source_payload_for_event(normalized_item),
        }
        reaction_target = self._reaction_target_for_task(normalized_item)
        if reaction_target:
            delegation_payload["reaction_target"] = reaction_target
        if isinstance(normalized_item.get("portal_start_reaction"), dict):
            delegation_payload["portal_start_reaction"] = normalized_item["portal_start_reaction"]
        if isinstance(normalized_item.get("portal_start_reaction_error"), dict):
            delegation_payload["portal_start_reaction_error"] = normalized_item["portal_start_reaction_error"]
        if isinstance(normalized_item.get("portal_start_reply"), dict):
            delegation_payload["portal_start_reply"] = normalized_item["portal_start_reply"]
        if isinstance(normalized_item.get("portal_start_reply_error"), dict):
            delegation_payload["portal_start_reply_error"] = normalized_item["portal_start_reply_error"]
        input_payload = {
            "schema": "agent_async_task.v1",
            "skill_name": skill_name,
            "task_session_id": task_session_id,
            "root_task_id": task_id,
            "parent_task_id": None,
            "delegation_rule_id": rule.id,
            "autonomous": True,
            "autonomous_instruction": AGENT_ASYNC_TASK_AUTONOMOUS_INSTRUCTION,
            "user_task": task_content,
            "delegation": delegation_payload,
        }
        try:
            task = self.task_repo.create(
                id=task_id,
                assignee_agent_id=rule.target_agent_id,
                owner_user_id=rule.owner_user_id,
                created_by_user_id=rule.created_by_user_id,
                source="delegation",
                task_type=AGENT_ASYNC_TASK_TYPE,
                task_family=AGENT_ASYNC_TASK_FAMILY,
                provider=provider,
                trigger=source,
                title=self._derive_task_title(task_content),
                skill_name=skill_name,
                parent_task_id=None,
                root_task_id=task_id,
                task_session_id=task_session_id,
                input_payload_json=json.dumps(input_payload),
                version_key=str(normalized_item.get("version_key") or "")[:255] or None,
                dedupe_key=agent_task_dedupe_key,
                status="queued",
                result_payload_json=None,
                retry_count=0,
            )
        except Exception as exc:
            self.repo.update_event_status(refreshed_event, status="failed", task_id=None, error_message=str(exc)[:500])
            raise

        refreshed_event = self.repo.get_event(event.id) or event
        self.repo.update_event_status(refreshed_event, status="task_created", task_id=task.id, error_message=None)
        self.dispatcher.dispatch_task_in_background(task.id)
        return task, False

    @staticmethod
    def _extract_task_result_text(task) -> str | None:
        payload = DelegationRuleService._parse_json(getattr(task, "result_payload_json", None))
        output_payload = payload.get("output_payload")
        if isinstance(output_payload, dict):
            for key in ("final_response", "response", "review_summary", "result_summary", "summary", "raw_text", "message"):
                value = output_payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        for key in ("final_response", "response", "summary", "raw_text", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        summary = getattr(task, "summary", None)
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        return None

    @staticmethod
    def _truthy_flag(value) -> bool:
        if value is True:
            return True
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return False

    @classmethod
    def _task_reply_handled_by_skill(cls, task) -> bool:
        payload = cls._parse_json(getattr(task, "result_payload_json", None))
        candidates = [payload]
        for key in ("output_payload", "normalized_payload"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                candidates.append(nested)

        for candidate in candidates:
            if cls._truthy_flag(candidate.get("reply_handled_by_skill")):
                return True
            external_actions = candidate.get("external_actions")
            if not isinstance(external_actions, list):
                continue
            for action in external_actions:
                if not isinstance(action, dict):
                    continue
                if str(action.get("type") or "").strip() != "reply_handled_by_skill":
                    continue
                status_value = str(action.get("status") or "").strip().lower()
                if status_value in {"success", "succeeded", "done", "completed", "ok"} or cls._truthy_flag(action.get("success")):
                    return True
        return False

    async def _process_pending_replies(self, rule) -> tuple[int, int]:
        sent_count = 0
        failed_count = 0
        events = self.repo.list_events_pending_reply(rule.id, limit=100)
        for event in events:
            if not event.task_id:
                continue
            task = self.task_repo.get_by_id(event.task_id)
            if not task or (task.status or "").strip().lower() != "done":
                continue
            normalized = self._parse_json(event.normalized_payload_json)
            reply_target = normalized.get("reply_target")
            if self._task_reply_handled_by_skill(task):
                logger.info(
                    "Skipping automatic delegation reply for event %s task %s source %s reply_target=%s: reply handled by skill",
                    event.id,
                    event.task_id,
                    normalized.get("source"),
                    reply_target,
                )
                event = self.repo.update_event_status(event, status="reply_sent", task_id=event.task_id, error_message=None)
                await self._cleanup_portal_start_reaction(rule=rule, event=event, normalized=normalized)
                sent_count += 1
                continue
            if not isinstance(reply_target, dict) or not reply_target:
                event = self.repo.update_event_status(event, status="reply_failed", task_id=event.task_id, error_message="Missing reply target")
                await self._cleanup_portal_start_reaction(rule=rule, event=event, normalized=normalized)
                failed_count += 1
                continue
            result_text = self._extract_task_result_text(task)
            if not result_text:
                event = self.repo.update_event_status(
                    event,
                    status="reply_failed",
                    task_id=event.task_id,
                    error_message="Task completed without reply text",
                )
                await self._cleanup_portal_start_reaction(rule=rule, event=event, normalized=normalized)
                failed_count += 1
                continue
            reply_provider = str((reply_target or {}).get("provider") or rule.source_type or "").strip().lower()
            if reply_provider == "github":
                reply_text = f"{delegation_reply_marker(rule.id, event.id)}\n\n{result_text}"
            else:
                reply_text = result_text
            try:
                await self.reply_service.send_reply(self.db, rule=rule, event=event, reply_target=reply_target, text=reply_text)
            except Exception as exc:
                event = self.repo.update_event_status(event, status="reply_failed", task_id=event.task_id, error_message=str(exc)[:500])
                await self._cleanup_portal_start_reaction(rule=rule, event=event, normalized=normalized)
                failed_count += 1
                continue
            event = self.repo.update_event_status(event, status="reply_sent", task_id=event.task_id, error_message=None)
            await self._cleanup_portal_start_reaction(rule=rule, event=event, normalized=normalized)
            sent_count += 1
        return sent_count, failed_count

    async def run_rule_once(self, rule_id: str, triggered_by: str = "api") -> RunOnceResult:
        rule = self.repo.get(rule_id)
        if not rule:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DelegationRule not found")
        if self.repo.is_deleted_rule(rule):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="DelegationRule is archived")

        source = self._validate_source(rule.trigger_type)
        provider = self._provider_for_source(source)
        schedule = self._parse_json(rule.schedule_json)
        interval_seconds = int(schedule.get("interval_seconds") or 60)
        run = self.repo.create_run(rule_id=rule.id)
        found_count = 0
        created_task_count = 0
        skipped_count = 0
        created_task_ids: list[str] = []
        item_error_count = 0
        reply_sent_count = 0
        reply_failed_count = 0
        try:
            self._validate_agent_provider_config(agent_id=rule.target_agent_id, provider=provider)
            reply_sent_count, reply_failed_count = await self._process_pending_replies(rule)
            poll_result = await self.source_poller.poll(self.db, rule)
            items = poll_result.items if hasattr(poll_result, "items") else list(poll_result or [])
            found_count = len(items)
            source_scope = normalize_delegation_source_scope(source, self._parse_json(rule.scope_json))
            source_conditions = normalize_delegation_source_conditions(source, self._parse_json(rule.trigger_config_json))
            condition_skipped_count = 0
            for item in items:
                matched, skip_reason = delegation_source_item_matches(source, item, source_scope, source_conditions)
                if not matched:
                    condition_skipped_count += 1
                    skipped_count += 1
                    logger.info(
                        "Skipping delegation source item by condition rule_id=%s source=%s reason=%s dedupe_key=%s",
                        rule.id,
                        source,
                        skip_reason,
                        str((item or {}).get("dedupe_key") or ""),
                    )
                    continue
                try:
                    task, skipped = await self._create_task_for_source_item(rule=rule, item=item)
                    if skipped:
                        skipped_count += 1
                        continue
                    created_task_count += 1
                    created_task_ids.append(task.id)
                except Exception as exc:
                    item_error_count += 1
                    dedupe_key = str((item or {}).get("dedupe_key") or "").strip()
                    if dedupe_key:
                        event = self.repo.get_event_by_dedupe(rule_id=rule.id, dedupe_key=dedupe_key)
                        if event:
                            self.repo.update_event_status(event, status="failed", task_id=event.task_id, error_message=str(exc)[:500])

            now = datetime.utcnow()
            state = self._parse_json(rule.state_json)
            state_patch = poll_result.state_patch if hasattr(poll_result, "state_patch") else {}
            if isinstance(state_patch, dict) and state_patch:
                state.update(state_patch)
            state["last_successful_poll_at"] = now.isoformat()
            run_status = "success"
            if item_error_count > 0 and created_task_count > 0:
                run_status = "partial"
            elif item_error_count > 0:
                run_status = "failed"
            self.repo.finish_run(
                run,
                status=run_status,
                found_count=found_count,
                created_task_count=created_task_count,
                skipped_count=skipped_count,
                metrics={
                    "triggered_by": triggered_by,
                    "created_task_ids": created_task_ids,
                    "error_count": item_error_count,
                    "condition_skipped_count": condition_skipped_count,
                    "reply_sent_count": reply_sent_count,
                    "reply_failed_count": reply_failed_count,
                },
            )
            self.repo.update(
                rule,
                {
                    "state_json": self._json(state),
                    "last_run_at": now,
                    "next_run_at": now + timedelta(seconds=interval_seconds) if rule.enabled else None,
                    "locked_until": None,
                },
            )
            return RunOnceResult(
                rule_id=rule.id,
                status=run_status,
                found_count=found_count,
                created_task_count=created_task_count,
                skipped_count=skipped_count,
                run_id=run.id,
                created_task_ids=created_task_ids,
            )
        except Exception as exc:
            self.repo.finish_run(
                run,
                status="failed",
                found_count=found_count,
                created_task_count=created_task_count,
                skipped_count=skipped_count,
                error_message=str(exc),
                metrics={"triggered_by": triggered_by},
            )
            now = datetime.utcnow()
            self.repo.update(
                rule,
                {
                    "last_run_at": now,
                    "next_run_at": now + timedelta(seconds=max(interval_seconds, 60)) if rule.enabled else None,
                    "locked_until": None,
                },
            )
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
