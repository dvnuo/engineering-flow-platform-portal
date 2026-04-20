import json
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.automation_rule_repo import AutomationRuleRepository
from app.schemas.automation_rule import AutomationRuleCreate, AutomationRuleUpdate
from app.services.github_pr_review_poller import GithubPrReviewPoller
from app.services.provider_config_resolver import ProviderConfigResolverError, resolve_github_for_agent
from app.services.task_dispatcher import TaskDispatcherService


@dataclass
class RunOnceResult:
    rule_id: str
    status: str
    found_count: int
    created_task_count: int
    skipped_count: int
    run_id: str
    created_task_ids: list[str]


class AutomationRuleService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = AutomationRuleRepository(db)
        self.task_repo = AgentTaskRepository(db)
        self.dispatcher = TaskDispatcherService()
        self.poller = GithubPrReviewPoller()

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
    def _build_from_structured(existing: dict | None, payload: dict) -> dict:
        merged = dict(existing or {})
        if "owner" in payload and payload["owner"] is not None:
            merged.setdefault("scope_json", {})
            merged["scope_json"]["owner"] = payload["owner"].strip()
        if "repo" in payload and payload["repo"] is not None:
            merged.setdefault("scope_json", {})
            merged["scope_json"]["repo"] = payload["repo"].strip()
        if "review_target_type" in payload and payload["review_target_type"] is not None:
            merged.setdefault("trigger_config_json", {})
            merged["trigger_config_json"]["review_target_type"] = payload["review_target_type"]
        if "review_target" in payload and payload["review_target"] is not None:
            merged.setdefault("trigger_config_json", {})
            merged["trigger_config_json"]["review_target"] = payload["review_target"].strip()
        if "interval_seconds" in payload and payload["interval_seconds"] is not None:
            merged.setdefault("schedule_json", {})
            merged["schedule_json"]["interval_seconds"] = payload["interval_seconds"]
        if "skill_name" in payload and payload["skill_name"] is not None:
            merged.setdefault("task_config_json", {})
            merged["task_config_json"]["skill_name"] = payload["skill_name"].strip()
        if "review_event" in payload and payload["review_event"] is not None:
            merged.setdefault("task_config_json", {})
            merged["task_config_json"]["review_event"] = payload["review_event"].strip()
        return merged

    def create_rule(self, payload: AutomationRuleCreate, current_user_id: int) -> object:
        data = payload.model_dump()
        try:
            resolve_github_for_agent(self.db, payload.target_agent_id)
        except ProviderConfigResolverError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        built = self._build_from_structured({}, data)
        interval = built.get("schedule_json", {}).get("interval_seconds", 60)
        now = datetime.utcnow()
        create_data = {
            "name": payload.name,
            "enabled": payload.enabled,
            "source_type": payload.source_type,
            "trigger_type": payload.trigger_type,
            "target_agent_id": payload.target_agent_id,
            "task_type": payload.task_type,
            "scope_json": json.dumps(built.get("scope_json", {})),
            "trigger_config_json": json.dumps(built.get("trigger_config_json", {})),
            "task_config_json": json.dumps(built.get("task_config_json", {})),
            "schedule_json": json.dumps(built.get("schedule_json", {"interval_seconds": 60})),
            "state_json": "{}",
            "owner_user_id": current_user_id,
            "created_by_user_id": current_user_id,
            "next_run_at": now + timedelta(seconds=interval),
        }
        return self.repo.create(create_data, current_user_id=current_user_id)

    def update_rule(self, rule, payload: AutomationRuleUpdate, current_user_id: int) -> object:
        data = payload.model_dump(exclude_unset=True)
        existing = {
            "scope_json": self._parse_json(rule.scope_json),
            "trigger_config_json": self._parse_json(rule.trigger_config_json),
            "task_config_json": self._parse_json(rule.task_config_json),
            "schedule_json": self._parse_json(rule.schedule_json),
        }
        built = self._build_from_structured(existing, data)

        update_data = {}
        for key in ["name", "enabled", "target_agent_id"]:
            if key in data:
                update_data[key] = data[key]
        if "target_agent_id" in update_data:
            try:
                resolve_github_for_agent(self.db, update_data["target_agent_id"])
            except ProviderConfigResolverError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        update_data["scope_json"] = json.dumps(built.get("scope_json", {}))
        update_data["trigger_config_json"] = json.dumps(built.get("trigger_config_json", {}))
        update_data["task_config_json"] = json.dumps(built.get("task_config_json", {}))
        update_data["schedule_json"] = json.dumps(built.get("schedule_json", {"interval_seconds": 60}))
        update_data["updated_at"] = datetime.utcnow()
        return self.repo.update(rule, update_data)

    def _stale_superseded_github_review_tasks_for_bundle(self, *, assignee_agent_id: str, bundle_id: str, new_head_sha: str | None, superseding_task_id: str) -> None:
        stale_payload = json.dumps(
            {
                "ok": False,
                "error_code": "superseded_by_new_head_sha",
                "message": "GitHub review task superseded by a newer PR head_sha",
                "superseded_by_task_id": superseding_task_id,
                "superseded_by_head_sha": new_head_sha,
            }
        )
        candidates = self.task_repo.list_active_tasks_for_bundle(
            assignee_agent_id=assignee_agent_id,
            bundle_id=bundle_id,
            task_type="github_review_task",
        )
        to_update = []
        for task in candidates:
            if task.id == superseding_task_id:
                continue
            if new_head_sha and task.version_key == new_head_sha:
                continue
            task.status = "stale"
            task.result_payload_json = stale_payload
            to_update.append(task)
        self.task_repo.save_all(to_update)

    @staticmethod
    def _agent_task_dedupe_key(full_dedupe_key: str) -> str:
        if len(full_dedupe_key) <= 240:
            return full_dedupe_key
        return "automation:" + hashlib.sha256(full_dedupe_key.encode()).hexdigest()

    def create_github_review_task_for_discovered_item(self, *, rule, item: dict, task_cfg: dict) -> tuple[object | None, bool]:
        owner = item.get("owner")
        repo = item.get("repo")
        pull_number = item.get("pull_number")
        head_sha = item.get("head_sha") or ""
        review_target_type = item.get("review_target", {}).get("type")
        review_target = item.get("review_target", {}).get("name")
        dedupe_key = f"github:pr_review_requested:{rule.id}:{owner}/{repo}:{pull_number}:{head_sha}:{review_target_type}:{review_target}"
        event = self.repo.get_event_by_dedupe(rule_id=rule.id, dedupe_key=dedupe_key)
        if event:
            status_text = (event.status or "").strip().lower()
            if status_text == "task_created" and event.task_id:
                return None, True
            if status_text == "failed" and event.task_id:
                return None, True
            if status_text not in {"discovered", "failed"}:
                return None, True
        else:
            event = self.repo.create_event(
                rule_id=rule.id,
                dedupe_key=dedupe_key,
                source_payload_json=json.dumps(item.get("source_payload") or {}),
                normalized_payload_json=json.dumps(item),
                status="discovered",
            )

        payload = {
            "source": "automation_rule",
            "automation_rule": "github.pr_review_requested",
            "automation_rule_id": rule.id,
            "rule_id": rule.id,
            "provider": "github",
            "owner": owner,
            "repo": repo,
            "pull_number": pull_number,
            "head_sha": head_sha,
            "review_target": {"type": review_target_type, "name": review_target},
            "task_type": rule.task_type,
            "skill_name": task_cfg.get("skill_name", "review-pull-request"),
            "review_event": task_cfg.get("review_event", "COMMENT"),
            "dedupe_key": dedupe_key,
        }
        bundle_id = f"github:pr_review:{owner}/{repo}:{pull_number}"
        task = self.task_repo.create(
            parent_agent_id=None,
            assignee_agent_id=rule.target_agent_id,
            owner_user_id=rule.owner_user_id,
            created_by_user_id=rule.created_by_user_id,
            source="automation_rule",
            task_type=rule.task_type,
            input_payload_json=json.dumps(payload),
            shared_context_ref=None,
            task_family="triggered_work",
            provider="github",
            trigger="github_pr_review_requested",
            bundle_id=bundle_id,
            version_key=head_sha,
            dedupe_key=self._agent_task_dedupe_key(dedupe_key),
            status="queued",
            result_payload_json=None,
            retry_count=0,
        )
        self._stale_superseded_github_review_tasks_for_bundle(
            assignee_agent_id=rule.target_agent_id,
            bundle_id=bundle_id,
            new_head_sha=head_sha,
            superseding_task_id=task.id,
        )
        self.repo.update_event_status(event, status="task_created", task_id=task.id, error_message=None)
        self.dispatcher.dispatch_task_in_background(task.id)
        return task, False

    async def run_rule_once(self, rule_id: str, triggered_by: str = "api") -> RunOnceResult:
        rule = self.repo.get(rule_id)
        if not rule:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AutomationRule not found")
        if self.repo.is_deleted_rule(rule):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="AutomationRule is archived")

        run = self.repo.create_run(rule_id=rule.id)
        scope = self._parse_json(rule.scope_json)
        trigger_cfg = self._parse_json(rule.trigger_config_json)
        task_cfg = self._parse_json(rule.task_config_json)
        schedule = self._parse_json(rule.schedule_json)
        interval_seconds = int(schedule.get("interval_seconds") or 60)

        found_count = 0
        created_task_count = 0
        skipped_count = 0
        created_task_ids: list[str] = []
        run_error_count = 0

        try:
            provider_cfg = resolve_github_for_agent(self.db, rule.target_agent_id)
            items = await self.poller.poll_review_requests(
                provider_config=provider_cfg,
                owner=str(scope.get("owner") or "").strip(),
                repo=str(scope.get("repo") or "").strip(),
                review_target_type=str(trigger_cfg.get("review_target_type") or "user").strip(),
                review_target=str(trigger_cfg.get("review_target") or "").strip(),
            )
            found_count = len(items)
            for item in items:
                try:
                    task, skipped = self.create_github_review_task_for_discovered_item(
                        rule=rule,
                        item=item,
                        task_cfg=task_cfg,
                    )
                    if skipped:
                        skipped_count += 1
                        continue
                    created_task_count += 1
                    created_task_ids.append(task.id)
                except Exception as item_exc:
                    run_error_count += 1
                    owner = item.get("owner")
                    repo = item.get("repo")
                    pull_number = item.get("pull_number")
                    head_sha = item.get("head_sha") or ""
                    review_target_type = item.get("review_target", {}).get("type")
                    review_target = item.get("review_target", {}).get("name")
                    dedupe_key = f"github:pr_review_requested:{rule.id}:{owner}/{repo}:{pull_number}:{head_sha}:{review_target_type}:{review_target}"
                    event = self.repo.get_event_by_dedupe(rule_id=rule.id, dedupe_key=dedupe_key)
                    if event:
                        self.repo.update_event_status(
                            event,
                            status="failed",
                            task_id=None,
                            error_message=str(item_exc)[:500],
                        )

            now = datetime.utcnow()
            run_status = "success"
            if run_error_count > 0 and created_task_count > 0:
                run_status = "partial"
            elif run_error_count > 0:
                run_status = "failed"
            self.repo.finish_run(
                run,
                status=run_status,
                found_count=found_count,
                created_task_count=created_task_count,
                skipped_count=skipped_count,
                metrics={"triggered_by": triggered_by, "created_task_ids": created_task_ids, "error_count": run_error_count},
            )
            self.repo.update(
                rule,
                {
                    "last_run_at": now,
                    "next_run_at": now + timedelta(seconds=interval_seconds),
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
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
