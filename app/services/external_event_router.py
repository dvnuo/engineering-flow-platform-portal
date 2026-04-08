import json
import asyncio
import threading

from app.db import SessionLocal
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.external_event_subscription_repo import ExternalEventSubscriptionRepository
from app.repositories.workflow_transition_rule_repo import WorkflowTransitionRuleRepository
from app.schemas.external_event_ingress import ExternalEventIngressRequest, ExternalEventIngressResponse
from app.services.capability_context_service import CapabilityContextService
from app.services.runtime_router import RuntimeRouterService
from app.services.task_dispatcher import TaskDispatcherService
from app.services.workflow_rule_config import parse_workflow_rule_config
from sqlalchemy.orm import Session


class ExternalEventRouterService:
    def __init__(self) -> None:
        self.runtime_router = RuntimeRouterService()
        self.task_dispatcher = TaskDispatcherService()
        self.capability_context_service = CapabilityContextService()

    @staticmethod
    def _normalize_source_type(source_type: str) -> str:
        return (source_type or "").strip().lower()

    @staticmethod
    def _derive_task_type(source_type: str, event_type: str) -> str:
        table = {
            ("jira", "issue_updated"): "jira_event_task",
            ("jira", "workflow_review_requested"): "jira_workflow_review_task",
            ("github", "pull_request_review_requested"): "github_review_task",
        }
        return table.get((source_type, event_type), event_type)

    @staticmethod
    def _matches_target_ref(subscription, target_ref: str | None) -> bool:
        if not subscription.target_ref:
            return True
        return subscription.target_ref == target_ref

    @staticmethod
    def _parse_json_object(raw: str | None) -> dict | None:
        if raw is None or not raw.strip():
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    @staticmethod
    def _extract_github_review_payload(request: ExternalEventIngressRequest, subscription_id: str) -> tuple[dict | None, str | None]:
        payload_obj = ExternalEventRouterService._parse_json_object(request.payload_json)
        if payload_obj is None:
            return None, "payload_json must be a JSON object for github pull_request_review_requested"

        owner = payload_obj.get("owner")
        repo = payload_obj.get("repo")
        pull_number = payload_obj.get("pull_number")
        if not owner or not repo or pull_number is None:
            return None, "github review event requires owner, repo, and pull_number in payload_json"

        return {
            "owner": owner,
            "repo": repo,
            "pull_number": pull_number,
            "reviewer": payload_obj.get("reviewer"),
            "head_sha": payload_obj.get("head_sha"),
            "comment": payload_obj.get("comment"),
            "event_type": request.event_type,
            "subscription_id": subscription_id,
            "metadata_json": request.metadata_json,
        }, None

    @staticmethod
    def _build_github_dedupe_hint(request: ExternalEventIngressRequest, payload: dict | None) -> str | None:
        if request.dedupe_key:
            return request.dedupe_key
        if request.source_type.strip().lower() != "github" or request.event_type != "pull_request_review_requested":
            return None
        if not payload:
            return None
        owner = payload.get("owner")
        repo = payload.get("repo")
        pull_number = payload.get("pull_number")
        if not owner or not repo or pull_number is None or not request.external_account_id:
            return None
        head_sha = payload.get("head_sha") or ""
        return f"github:review:{owner}/{repo}:{pull_number}:{request.external_account_id}:{head_sha}"

    @staticmethod
    def _build_github_review_family_prefix(request: ExternalEventIngressRequest, payload: dict | None) -> str | None:
        if request.source_type.strip().lower() != "github" or request.event_type != "pull_request_review_requested":
            return None
        if not payload:
            return None
        owner = payload.get("owner")
        repo = payload.get("repo")
        pull_number = payload.get("pull_number")
        if not owner or not repo or pull_number is None or not request.external_account_id:
            return None
        return f"github:review:{owner}/{repo}:{pull_number}:{request.external_account_id}:"

    @staticmethod
    def _extract_github_head_sha_from_dedupe_key(dedupe_key: str | None) -> str | None:
        if not dedupe_key:
            return None
        if not dedupe_key.startswith("github:review:"):
            return None
        return dedupe_key.rsplit(":", 1)[-1]

    @staticmethod
    def _build_github_superseded_payload(*, superseding_task_id: str, new_head_sha: str | None) -> str:
        return json.dumps(
            {
                "ok": False,
                "error_code": "superseded_by_new_head_sha",
                "message": "GitHub review task superseded by a newer PR head_sha",
                "superseded_by_task_id": superseding_task_id,
                "superseded_by_head_sha": new_head_sha,
            }
        )

    def _stale_superseded_github_review_tasks(
        self,
        *,
        task_repo: AgentTaskRepository,
        assignee_agent_id: str,
        family_prefix: str | None,
        new_dedupe_key: str | None,
        new_head_sha: str | None,
        superseding_task_id: str,
    ) -> None:
        if not family_prefix:
            return
        candidates = task_repo.list_active_github_review_tasks_for_family(
            assignee_agent_id=assignee_agent_id,
            family_prefix=family_prefix,
        )
        stale_payload = self._build_github_superseded_payload(
            superseding_task_id=superseding_task_id,
            new_head_sha=new_head_sha,
        )
        to_update = []
        for task in candidates:
            if task.id == superseding_task_id:
                continue
            if new_dedupe_key and task.shared_context_ref == new_dedupe_key:
                continue
            task.status = "stale"
            task.result_payload_json = stale_payload
            to_update.append(task)
        task_repo.save_all(to_update)

    @staticmethod
    def _is_allowed_github_repo(subscription, owner: str, repo: str) -> bool:
        config_obj = ExternalEventRouterService._parse_json_object(subscription.config_json)
        if not config_obj:
            return True
        allowed_repos = config_obj.get("allowed_repos")
        if allowed_repos is None:
            return True
        if not isinstance(allowed_repos, list):
            return False
        target = f"{owner}/{repo}"
        return any(isinstance(item, str) and item == target for item in allowed_repos)

    def _evaluate_capability_profile_event_gate(
        self,
        *,
        agent,
        source_type: str,
        event_type: str,
        db: Session,
        capability_context=None,
    ) -> dict | None:
        if not agent:
            return None

        context = capability_context
        if context is None:
            profile_id, resolved_profile = self.capability_context_service.resolve_for_agent(db, agent)
            context = self.capability_context_service.build_runtime_capability_context(
                profile_id, resolved_profile, db=db, agent_id=agent.id
            )

        if isinstance(context, dict):
            allowed_external_systems = context.get("allowed_external_systems", [])
            allowed_webhook_triggers = context.get("allowed_webhook_triggers", [])
        else:
            allowed_external_systems = getattr(context, "allowed_external_systems", []) or []
            allowed_webhook_triggers = getattr(context, "allowed_webhook_triggers", []) or []

        if allowed_external_systems and source_type not in allowed_external_systems:
            return {
                "routing_reason": "external_system_not_allowed",
                "message": "Matched agent capability profile does not allow this source_type",
            }
        if allowed_webhook_triggers and event_type not in allowed_webhook_triggers:
            return {
                "routing_reason": "webhook_trigger_not_allowed",
                "message": "Matched agent capability profile does not allow this event_type",
            }
        return None

    def route_external_event(self, request: ExternalEventIngressRequest, db: Session) -> ExternalEventIngressResponse:
        source_type = self._normalize_source_type(request.source_type)
        subscription_repo = ExternalEventSubscriptionRepository(db)
        workflow_rule_repo = WorkflowTransitionRuleRepository(db)
        task_repo = AgentTaskRepository(db)

        subscriptions = subscription_repo.list_enabled_for_source(source_type=source_type, event_type=request.event_type)
        matching_subscriptions = [sub for sub in subscriptions if self._matches_target_ref(sub, request.target_ref)]
        matching_subscriptions.sort(key=lambda item: item.id)
        matched_subscription_ids = [sub.id for sub in matching_subscriptions]

        if not matching_subscriptions:
            return ExternalEventIngressResponse(
                accepted=False,
                matched_subscription_ids=[],
                routing_reason="no_matching_subscription",
                resolved_task_type=None,
                message="No enabled subscription matched source/event/target_ref",
            )

        matched_agent_id = None
        matched_workflow_rule_id = None
        selected_subscription = matching_subscriptions[0]

        if source_type == "jira":
            if not request.project_key or not request.issue_type or not request.trigger_status:
                return ExternalEventIngressResponse(
                    accepted=False,
                    matched_subscription_ids=matched_subscription_ids,
                    routing_reason="missing_jira_workflow_context",
                    message="project_key, issue_type, and trigger_status are required for jira workflow routing",
                )

            matched_rule = workflow_rule_repo.find_matching_jira_rule(
                project_key=request.project_key,
                issue_type=request.issue_type,
                trigger_status=request.trigger_status,
                assignee_binding=request.issue_assignee,
            )
            if not matched_rule:
                return ExternalEventIngressResponse(
                    accepted=False,
                    matched_subscription_ids=matched_subscription_ids,
                    routing_reason="no_matching_workflow_rule",
                    message="No enabled jira workflow transition rule matched the event context",
                )

            matched_workflow_rule_id = matched_rule.id
            matched_agent_id = matched_rule.target_agent_id
            task_type = "jira_workflow_review_task"
            matched_agent = AgentRepository(db).get_by_id(matched_agent_id) if matched_agent_id else None
            gate_rejection = self._evaluate_capability_profile_event_gate(
                agent=matched_agent,
                source_type=source_type,
                event_type=request.event_type,
                db=db,
            )
            if gate_rejection:
                return ExternalEventIngressResponse(
                    accepted=False,
                    matched_subscription_ids=matched_subscription_ids,
                    routing_reason=gate_rejection["routing_reason"],
                    matched_agent_id=matched_agent_id,
                    matched_workflow_rule_id=matched_workflow_rule_id,
                    resolved_task_type=task_type,
                    message=gate_rejection["message"],
                )
            _normalized_config_json, parsed_workflow_context, config_error = parse_workflow_rule_config(matched_rule.config_json)
            if config_error:
                return ExternalEventIngressResponse(
                    accepted=False,
                    matched_subscription_ids=matched_subscription_ids,
                    routing_reason="invalid_workflow_rule_config",
                    matched_workflow_rule_id=matched_rule.id,
                    resolved_task_type=task_type,
                    message="Matched workflow rule has invalid config_json",
                )

            payload = {
                "issue_key": request.issue_key,
                "project_key": request.project_key,
                "issue_type": request.issue_type,
                "trigger_status": request.trigger_status,
                "issue_assignee": request.issue_assignee,
                "skill_name": matched_rule.skill_name,
                "success_transition": matched_rule.success_transition,
                "failure_transition": matched_rule.failure_transition,
                "success_reassign_to": matched_rule.success_reassign_to,
                "failure_reassign_to": matched_rule.failure_reassign_to,
                "explicit_success_assignee": matched_rule.explicit_success_assignee,
                "explicit_failure_assignee": matched_rule.explicit_failure_assignee,
                "workflow_rule_id": matched_rule.id,
                "workflow_context": parsed_workflow_context or {},
                "payload_json": request.payload_json,
                "metadata_json": request.metadata_json,
            }
            input_payload_json = json.dumps(payload)
            routing_reason = "matched_workflow_rule"
        else:
            if not request.external_account_id:
                return ExternalEventIngressResponse(
                    accepted=False,
                    matched_subscription_ids=matched_subscription_ids,
                    routing_reason="missing_external_account_id",
                    message="external_account_id is required for identity binding based routing",
                )

            routing_decision = self.runtime_router.resolve_binding_decision_for_event(
                system_type=source_type,
                external_account_id=request.external_account_id,
                db=db,
            )
            if not routing_decision.matched_agent_id:
                return ExternalEventIngressResponse(
                    accepted=False,
                    matched_subscription_ids=matched_subscription_ids,
                    routing_reason=routing_decision.reason,
                    matched_agent_id=None,
                    message="No agent matched the provided identity binding",
                )

            matched_agent_id = routing_decision.matched_agent_id
            matched_agent = AgentRepository(db).get_by_id(matched_agent_id)
            gate_rejection = self._evaluate_capability_profile_event_gate(
                agent=matched_agent,
                source_type=source_type,
                event_type=request.event_type,
                db=db,
                capability_context=routing_decision.capability_context,
            )
            if gate_rejection:
                return ExternalEventIngressResponse(
                    accepted=False,
                    matched_subscription_ids=matched_subscription_ids,
                    routing_reason=gate_rejection["routing_reason"],
                    matched_agent_id=matched_agent_id,
                    message=gate_rejection["message"],
                )
            task_type = self._derive_task_type(source_type, request.event_type)
            if source_type == "github" and request.event_type == "pull_request_review_requested":
                github_payload, github_error = self._extract_github_review_payload(request, selected_subscription.id)
                if github_error:
                    return ExternalEventIngressResponse(
                        accepted=False,
                        matched_subscription_ids=matched_subscription_ids,
                        routing_reason="invalid_github_event_payload",
                        matched_agent_id=matched_agent_id,
                        resolved_task_type=task_type,
                        message=github_error,
                    )
                if not self._is_allowed_github_repo(selected_subscription, github_payload["owner"], github_payload["repo"]):
                    return ExternalEventIngressResponse(
                        accepted=False,
                        matched_subscription_ids=matched_subscription_ids,
                        routing_reason="repo_not_allowed",
                        matched_agent_id=matched_agent_id,
                        resolved_task_type=task_type,
                        message="Repository is not allowed by subscription config_json.allowed_repos",
                    )
                input_payload_json = json.dumps(github_payload)
            else:
                input_payload_json = request.payload_json
            routing_reason = routing_decision.reason

        dedupe_hint = request.dedupe_key
        github_family_prefix = None
        if source_type == "jira":
            shared_context_ref = request.issue_key or request.dedupe_key or request.target_ref
        else:
            payload_obj = self._parse_json_object(input_payload_json)
            dedupe_hint = self._build_github_dedupe_hint(request, payload_obj) or request.dedupe_key
            github_family_prefix = self._build_github_review_family_prefix(request, payload_obj)
            shared_context_ref = dedupe_hint or request.target_ref

        if dedupe_hint:
            duplicate = task_repo.find_recent_duplicate(
                assignee_agent_id=matched_agent_id,
                source=source_type,
                task_type=task_type,
                dedupe_hint=dedupe_hint,
                input_payload_json=input_payload_json,
            )
            if duplicate:
                return ExternalEventIngressResponse(
                    accepted=True,
                    matched_subscription_ids=matched_subscription_ids,
                    routing_reason="duplicate_task",
                    matched_agent_id=matched_agent_id,
                    created_task_id=duplicate.id,
                    matched_workflow_rule_id=matched_workflow_rule_id,
                    resolved_task_type=task_type,
                    deduped=True,
                    message="Duplicate event detected; existing task reused",
                )

        task = task_repo.create(
            parent_agent_id=None,
            assignee_agent_id=matched_agent_id,
            source=source_type,
            task_type=task_type,
            input_payload_json=input_payload_json,
            shared_context_ref=shared_context_ref,
            status="queued",
            result_payload_json=None,
            retry_count=0,
        )

        if source_type == "github" and request.event_type == "pull_request_review_requested":
            self._stale_superseded_github_review_tasks(
                task_repo=task_repo,
                assignee_agent_id=matched_agent_id,
                family_prefix=github_family_prefix,
                new_dedupe_key=dedupe_hint,
                new_head_sha=self._extract_github_head_sha_from_dedupe_key(dedupe_hint),
                superseding_task_id=task.id,
            )

        should_dispatch = source_type == "jira" or (source_type == "github" and request.event_type == "pull_request_review_requested")
        if should_dispatch:
            self._dispatch_task_in_background(task.id)

            return ExternalEventIngressResponse(
                accepted=True,
                matched_subscription_ids=matched_subscription_ids,
                routing_reason=routing_reason,
                matched_agent_id=matched_agent_id,
                created_task_id=task.id,
                matched_workflow_rule_id=matched_workflow_rule_id,
                resolved_task_type=task_type,
                deduped=False,
                message="Event routed, task created, and scheduled for background dispatch",
            )

        return ExternalEventIngressResponse(
            accepted=True,
            matched_subscription_ids=matched_subscription_ids,
            routing_reason=routing_reason,
            matched_agent_id=matched_agent_id,
            created_task_id=task.id,
            matched_workflow_rule_id=matched_workflow_rule_id,
            resolved_task_type=task_type,
            deduped=False,
            message="Event routed and task created",
        )

    def _dispatch_task_in_background(self, task_id: str) -> None:
        # Dispatch runs out-of-band so webhook ACK is control-plane only and never waits on runtime completion.
        def _runner() -> None:
            db_session = SessionLocal()
            try:
                asyncio.run(self.task_dispatcher.dispatch_task(task_id, db_session))
            finally:
                db_session.close()

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
