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
from app.services.capability_context_service import CapabilityContextService
from app.services.github_pr_review_poller import GithubPrReviewPoller
from app.services.provider_config_resolver import ProviderConfigResolverError, resolve_github_for_agent
from app.services.task_dispatcher import TaskDispatcherService
from app.services.task_template_registry import build_agent_task_create_payload_from_template, require_task_template


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
        self.capability_context_service = CapabilityContextService()

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
        if "scope" in payload and isinstance(payload["scope"], dict):
            merged["scope_json"] = dict(payload["scope"])
        if "trigger_config" in payload and isinstance(payload["trigger_config"], dict):
            merged["trigger_config_json"] = dict(payload["trigger_config"])
        if "task_input_defaults" in payload and isinstance(payload["task_input_defaults"], dict):
            merged["task_config_json"] = dict(payload["task_input_defaults"])
        if "schedule" in payload and isinstance(payload["schedule"], dict):
            merged["schedule_json"] = dict(payload["schedule"])
        return merged

    def _validate_built_rule_config(self, *, built: dict, task_template_id: str) -> None:
        require_task_template(task_template_id)
        scope = built.get("scope_json") or {}
        trigger = built.get("trigger_config_json") or {}
        task = built.get("task_config_json") or {}

        owner = str(scope.get("owner") or "").strip()
        repo = str(scope.get("repo") or "").strip()
        target_type = str(trigger.get("review_target_type") or "").strip().lower()
        target = str(trigger.get("review_target") or "").strip()
        skill_name = str(task.get("skill_name") or "review-pull-request").strip()
        review_event = str(task.get("review_event") or "COMMENT").strip().upper()

        if task_template_id != "github_pr_review":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="github_pr_review_requested trigger requires github_pr_review task template")
        if not owner:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope.owner must not be empty")
        if not repo:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope.repo must not be empty")
        if target_type not in {"user", "team"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="trigger_config.review_target_type must be 'user' or 'team'")
        if not target:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="trigger_config.review_target must not be empty")
        if target_type == "user" and any(ch.isspace() for ch in target):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="review_target must not contain whitespace for user target")
        if not skill_name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="skill_name must not be empty")
        if review_event not in {"COMMENT", "APPROVE", "REQUEST_CHANGES"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="review_event must be one of: APPROVE, COMMENT, REQUEST_CHANGES")

    def create_rule(self, payload: AutomationRuleCreate, current_user_id: int) -> object:
        data = payload.model_dump()
        try:
            require_task_template(payload.task_template_id)
            resolve_github_for_agent(self.db, payload.target_agent_id)
        except ProviderConfigResolverError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        built = self._build_from_structured(
            {
                "scope_json": data.get("scope") or {},
                "trigger_config_json": data.get("trigger_config") or {},
                "task_config_json": data.get("task_input_defaults") or {},
                "schedule_json": data.get("schedule") or {"interval_seconds": 60},
            },
            {},
        )
        self._validate_built_rule_config(built=built, task_template_id=payload.task_template_id)
        skill_name = str((built.get("task_config_json") or {}).get("skill_name") or "review-pull-request").strip()
        self._validate_agent_can_run_github_pr_review_rule(agent_id=payload.target_agent_id, skill_name=skill_name)
        interval = int((built.get("schedule_json") or {}).get("interval_seconds") or 60)
        now = datetime.utcnow()
        create_data = {
            "name": payload.name,
            "enabled": payload.enabled,
            "source_type": payload.source_type,
            "trigger_type": payload.trigger_type,
            "target_agent_id": payload.target_agent_id,
            "task_type": "github_review_task",
            "task_template_id": payload.task_template_id,
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
        task_template_id = data.get("task_template_id") or rule.task_template_id
        self._validate_built_rule_config(built=built, task_template_id=task_template_id)

        update_data = {}
        for key in ["name", "enabled", "target_agent_id"]:
            if key in data:
                update_data[key] = data[key]
        update_data["task_template_id"] = task_template_id
        update_data["task_type"] = "github_review_task"

        target_agent_id = update_data.get("target_agent_id") or rule.target_agent_id
        try:
            resolve_github_for_agent(self.db, target_agent_id)
        except ProviderConfigResolverError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        skill_name = str((built.get("task_config_json") or {}).get("skill_name") or "review-pull-request").strip()
        self._validate_agent_can_run_github_pr_review_rule(agent_id=target_agent_id, skill_name=skill_name)

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

    def _validate_agent_can_run_github_pr_review_rule(self, *, agent_id: str, skill_name: str) -> None:
        agent = AgentRepository(self.db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Target agent not found")

        profile_id, resolved = self.capability_context_service.resolve_for_agent(self.db, agent)
        context = self.capability_context_service.build_runtime_capability_context(
            profile_id,
            resolved,
            db=self.db,
            agent_id=agent.id,
        )

        allowed_external_systems = {str(item).strip().lower() for item in context.get("allowed_external_systems", []) if str(item).strip()}
        if allowed_external_systems and "github" not in allowed_external_systems:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected agent capability profile does not allow GitHub PR review automation")

        allowed_webhook_triggers = {str(item).strip().lower() for item in context.get("allowed_webhook_triggers", []) if str(item).strip()}
        if allowed_webhook_triggers and not ({"pull_request_review_requested", "github_pr_review_requested", "github.pull_request_review_requested"} & allowed_webhook_triggers):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected agent capability profile does not allow GitHub PR review automation")

        if context.get("skill_set"):
            skill_allowance = self.capability_context_service.get_skill_allowance_detail(self.db, agent, skill_name)
            if not skill_allowance.allowed:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected agent capability profile does not allow GitHub PR review automation")

        allowed_actions = {str(item).strip().lower() for item in context.get("allowed_actions", []) if str(item).strip()}
        allowed_adapter_actions = {str(item).strip().lower() for item in context.get("allowed_adapter_actions", []) if str(item).strip()}
        if (allowed_actions or allowed_adapter_actions) and not ({"review_pull_request", "adapter:github:review_pull_request"} & (allowed_actions | allowed_adapter_actions)):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected agent capability profile does not allow GitHub PR review automation")

    def create_github_review_task_for_discovered_item(self, *, rule, item: dict, task_cfg: dict) -> tuple[object | None, bool]:
        owner = item.get("owner")
        repo = item.get("repo")
        pull_number = item.get("pull_number")
        head_sha = item.get("head_sha") or ""
        review_target_type = item.get("review_target", {}).get("type")
        review_target = item.get("review_target", {}).get("name")
        dedupe_key = f"github:pr_review_requested:{rule.id}:{owner}/{repo}:{pull_number}:{head_sha}:{review_target_type}:{review_target}"
        agent_task_dedupe_key = self._agent_task_dedupe_key(dedupe_key)
        event, _created = self.repo.get_or_create_event_by_dedupe(
            rule_id=rule.id,
            dedupe_key=dedupe_key,
            source_payload_json=json.dumps(item.get("source_payload") or {}),
            normalized_payload_json=json.dumps(item),
            status="discovered",
        )
        status_text = (event.status or "").strip().lower()
        if status_text == "task_created" and event.task_id:
            return None, True
        if status_text == "failed" and event.task_id:
            return None, True
        if status_text not in {"discovered", "failed", "creating_task"}:
            return None, True
        if not self.repo.claim_event_for_task_creation(event.id):
            refreshed = self.repo.get_event(event.id)
            refreshed_status = ((refreshed.status if refreshed else "") or "").strip().lower()
            if refreshed and refreshed_status == "task_created" and refreshed.task_id:
                return None, True
            return None, True

        refreshed = self.repo.get_event(event.id) or event
        existing_task = self.task_repo.find_by_dedupe_key(
            assignee_agent_id=rule.target_agent_id,
            source="automation_rule",
            task_type=rule.task_type,
            dedupe_key=agent_task_dedupe_key,
        )
        if existing_task:
            self.repo.update_event_status(refreshed, status="task_created", task_id=existing_task.id, error_message=None)
            return None, True

        template_id = getattr(rule, "task_template_id", None) or "github_pr_review"
        payload = build_agent_task_create_payload_from_template(
            template_id,
            {
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
                "review_target_type": review_target_type,
                "review_event": task_cfg.get("review_event", "COMMENT"),
                "skill_name": task_cfg.get("skill_name", "review-pull-request"),
                "writeback_mode": task_cfg.get("writeback_mode"),
                "dedupe_key": dedupe_key,
            },
            rule.target_agent_id,
        )
        bundle_id = f"github:pr_review:{owner}/{repo}:{pull_number}"
        try:
            task = self.task_repo.create(
                parent_agent_id=None,
                assignee_agent_id=rule.target_agent_id,
                owner_user_id=rule.owner_user_id,
                created_by_user_id=rule.created_by_user_id,
                source="automation_rule",
                task_type=payload["task_type"],
                template_id=template_id,
                input_payload_json=json.dumps(payload["input_payload_json"]),
                shared_context_ref=None,
                task_family=payload.get("task_family") or "triggered_work",
                provider=payload.get("provider") or "github",
                trigger=payload.get("trigger") or "github_pr_review_requested",
                bundle_id=bundle_id,
                version_key=head_sha,
                dedupe_key=agent_task_dedupe_key,
                status="queued",
                result_payload_json=None,
                retry_count=0,
            )
        except Exception as exc:
            refreshed = self.repo.get_event(event.id)
            if refreshed:
                self.repo.update_event_status(refreshed, status="failed", task_id=None, error_message=str(exc)[:500])
            raise
        self._stale_superseded_github_review_tasks_for_bundle(
            assignee_agent_id=rule.target_agent_id,
            bundle_id=bundle_id,
            new_head_sha=head_sha,
            superseding_task_id=task.id,
        )
        refreshed = self.repo.get_event(event.id) or event
        self.repo.update_event_status(refreshed, status="task_created", task_id=task.id, error_message=None)
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
        skill_name = str(task_cfg.get("skill_name") or "").strip() or "review-pull-request"

        found_count = 0
        created_task_count = 0
        skipped_count = 0
        created_task_ids: list[str] = []
        run_error_count = 0

        try:
            self._validate_agent_can_run_github_pr_review_rule(agent_id=rule.target_agent_id, skill_name=skill_name)
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
            now = datetime.utcnow()
            self.repo.update(
                rule,
                {
                    "last_run_at": now,
                    "next_run_at": now + timedelta(seconds=max(interval_seconds, 60)),
                    "locked_until": None,
                },
            )
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
