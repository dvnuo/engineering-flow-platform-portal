import json

from sqlalchemy.orm import Session

from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.automation_rule_repo import AutomationRuleRepository
from app.repositories.workflow_transition_rule_repo import WorkflowTransitionRuleRepository
from app.schemas.external_event_ingress import ExternalEventIngressRequest, ExternalEventIngressResponse
from app.services.automation_rule_service import AutomationRuleService
from app.services.capability_context_service import CapabilityContextService
from app.services.runtime_router import RuntimeRouterService
from app.services.task_dispatcher import TaskDispatcherService
from app.services.workflow_rule_config import parse_workflow_rule_config


class ExternalEventRouterService:
    def __init__(self) -> None:
        self.runtime_router = RuntimeRouterService()
        self.task_dispatcher = TaskDispatcherService()
        self.capability_context_service = CapabilityContextService()

    @staticmethod
    def _normalize_source_type(source_type: str) -> str:
        return (source_type or "").strip().lower()

    @staticmethod
    def _parse_json_object(raw: str | None) -> dict | None:
        if raw is None or not raw.strip():
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _parse_metadata_object(raw: str | None) -> dict | None:
        return ExternalEventRouterService._parse_json_object(raw)

    @staticmethod
    def _normalize_github_match_value(value: object) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _is_removed_legacy_provider_automation_event(source_type: str, event_type: str) -> bool:
        return (source_type, event_type) in {
            ("github", "mention"),
            ("jira", "assigned"),
            ("jira", "mention"),
            ("confluence", "mention"),
        }

    @staticmethod
    def _parse_event_payload_object(request: ExternalEventIngressRequest) -> dict | None:
        return ExternalEventRouterService._parse_json_object(request.payload_json)

    def _route_github_pr_review_requested_via_automation_rule(
        self,
        request: ExternalEventIngressRequest,
        db: Session,
    ) -> ExternalEventIngressResponse:
        payload_obj = self._parse_event_payload_object(request)
        if payload_obj is None:
            return ExternalEventIngressResponse(
                accepted=False,
                matched_subscription_ids=[],
                routing_reason="invalid_github_event_payload",
                resolved_task_type="github_review_task",
                message="payload_json must be a JSON object for github pull_request_review_requested",
            )

        owner = str(payload_obj.get("owner") or "").strip()
        repo = str(payload_obj.get("repo") or "").strip()
        normalized_owner = self._normalize_github_match_value(owner)
        normalized_repo = self._normalize_github_match_value(repo)
        pull_number = payload_obj.get("pull_number")
        missing = []
        if not owner:
            missing.append("owner")
        if not repo:
            missing.append("repo")
        if pull_number is None:
            missing.append("pull_number")
        if missing:
            return ExternalEventIngressResponse(
                accepted=False,
                matched_subscription_ids=[],
                routing_reason="invalid_github_event_payload",
                resolved_task_type="github_review_task",
                message=f"github review event missing required payload fields: {', '.join(missing)}",
            )

        metadata_obj = self._parse_metadata_object(request.metadata_json) or {}
        review_target_obj = payload_obj.get("review_target") if isinstance(payload_obj.get("review_target"), dict) else {}
        review_target_name = str(review_target_obj.get("name") or "").strip()
        review_target_type = str(
            review_target_obj.get("type")
            or payload_obj.get("review_target_type")
            or ""
        ).strip().lower()
        reviewer = str(payload_obj.get("reviewer") or "").strip()
        review_team = str(payload_obj.get("review_team") or "").strip()
        external_account_id = str(request.external_account_id or "").strip()
        provider_review_target_kind = str(metadata_obj.get("provider_review_target_kind") or "").strip().lower()
        binding_lookup_username = str(metadata_obj.get("binding_lookup_username") or "").strip()

        candidate_targets: list[tuple[str | None, str]] = []
        if review_target_name:
            candidate_targets.append((review_target_type or None, review_target_name))
        elif reviewer:
            candidate_targets.append(("user", reviewer))
        elif review_team:
            candidate_targets.append(("team", review_team))
        elif external_account_id:
            candidate_targets.append((provider_review_target_kind or None, external_account_id))

        if external_account_id:
            candidate_targets.append((None, external_account_id))
        if binding_lookup_username:
            candidate_targets.append((None, binding_lookup_username))

        deduped_candidates: list[tuple[str | None, str]] = []
        seen: set[tuple[str | None, str]] = set()
        for candidate_type, candidate_name in candidate_targets:
            normalized_name = self._normalize_github_match_value(candidate_name)
            normalized_type = (candidate_type or "").strip().lower() or None
            if not normalized_name:
                continue
            key = (normalized_type, normalized_name)
            if key in seen:
                continue
            seen.add(key)
            deduped_candidates.append(key)

        if not deduped_candidates:
            return ExternalEventIngressResponse(
                accepted=False,
                matched_subscription_ids=[],
                routing_reason="no_matching_automation_rule",
                resolved_task_type="github_review_task",
                message="No enabled GitHub PR reviewer automation rule matched this event",
            )

        candidate_names = {name for _, name in deduped_candidates}
        candidate_types_by_name: dict[str, set[str]] = {}
        for candidate_type, candidate_name in deduped_candidates:
            if candidate_type:
                candidate_types_by_name.setdefault(candidate_name, set()).add(candidate_type)

        rule_repo = AutomationRuleRepository(db)
        matched_rule = None
        matched_owner = owner
        matched_repo = repo
        matched_target_type = None
        matched_target_name = None
        for rule in rule_repo.list_enabled_for_trigger(
            source_type="github",
            trigger_type="github_pr_review_requested",
            task_type="github_review_task",
        ):
            scope_obj = self._parse_json_object(rule.scope_json) or {}
            rule_owner = str(scope_obj.get("owner") or "").strip()
            rule_repo = str(scope_obj.get("repo") or "").strip()
            if self._normalize_github_match_value(rule_owner) != normalized_owner:
                continue
            if self._normalize_github_match_value(rule_repo) != normalized_repo:
                continue
            trigger_obj = self._parse_json_object(rule.trigger_config_json) or {}
            rule_target_type = str(trigger_obj.get("review_target_type") or "").strip().lower()
            rule_target_name = str(trigger_obj.get("review_target") or "").strip()
            normalized_rule_target_name = self._normalize_github_match_value(rule_target_name)
            if not normalized_rule_target_name or normalized_rule_target_name not in candidate_names:
                continue
            matched_types = candidate_types_by_name.get(normalized_rule_target_name, set())
            if matched_types and rule_target_type not in matched_types:
                continue
            matched_rule = rule
            matched_owner = rule_owner
            matched_repo = rule_repo
            matched_target_type = rule_target_type or "user"
            matched_target_name = rule_target_name
            break

        if not matched_rule:
            return ExternalEventIngressResponse(
                accepted=False,
                matched_subscription_ids=[],
                routing_reason="no_matching_automation_rule",
                resolved_task_type="github_review_task",
                message="No enabled GitHub PR reviewer automation rule matched this event",
            )

        item = {
            "owner": matched_owner,
            "repo": matched_repo,
            "pull_number": pull_number,
            "html_url": payload_obj.get("html_url"),
            "title": payload_obj.get("title"),
            "head_sha": payload_obj.get("head_sha") or "",
            "review_target": {"type": matched_target_type, "name": matched_target_name},
            "source_payload": payload_obj,
        }
        task_cfg = self._parse_json_object(matched_rule.task_config_json) or {}
        skill_name = str(task_cfg.get("skill_name") or "").strip() or "review-pull-request"
        automation_service = AutomationRuleService(db)
        try:
            automation_service._validate_agent_can_run_github_pr_review_rule(
                agent_id=matched_rule.target_agent_id,
                skill_name=skill_name,
            )
        except Exception as exc:
            return ExternalEventIngressResponse(
                accepted=False,
                matched_subscription_ids=[],
                routing_reason="capability_profile_blocked",
                matched_agent_id=matched_rule.target_agent_id,
                resolved_task_type="github_review_task",
                message=str(getattr(exc, "detail", None) or "Selected agent capability profile does not allow GitHub PR review automation"),
            )

        task, skipped = automation_service.create_github_review_task_for_discovered_item(rule=matched_rule, item=item, task_cfg=task_cfg)
        if skipped:
            return ExternalEventIngressResponse(
                accepted=True,
                matched_subscription_ids=[],
                routing_reason="duplicate_automation_event",
                matched_agent_id=matched_rule.target_agent_id,
                resolved_task_type="github_review_task",
                deduped=True,
                message="Duplicate event detected; existing automation event reused",
            )
        return ExternalEventIngressResponse(
            accepted=True,
            matched_subscription_ids=[],
            routing_reason="matched_automation_rule",
            matched_agent_id=matched_rule.target_agent_id,
            created_task_id=task.id,
            resolved_task_type="github_review_task",
            deduped=False,
            message="Event routed via matching automation rule",
        )

    @staticmethod
    def _build_bundle_id(*, request: ExternalEventIngressRequest, source_type: str, payload_obj: dict | None, task_type: str) -> str | None:
        target_ref = (request.target_ref or "").strip()

        if source_type == "github" and task_type == "github_review_task":
            owner = payload_obj.get("owner") if payload_obj else None
            repo = payload_obj.get("repo") if payload_obj else None
            pull_number = payload_obj.get("pull_number") if payload_obj else None
            if owner and repo and pull_number is not None:
                return f"github:pr:{owner}/{repo}:{pull_number}"

        if source_type == "jira" and task_type == "jira_workflow_review_task":
            if request.issue_key:
                return f"jira:issue:{request.issue_key}"
            if target_ref:
                return f"jira:project:{target_ref}"

        fallback = target_ref or request.external_account_id or request.event_type
        return f"{source_type}:{fallback}" if fallback else None

    @staticmethod
    def _build_version_key(*, request: ExternalEventIngressRequest, source_type: str, payload_obj: dict | None, task_type: str) -> str | None:
        if source_type == "github" and task_type == "github_review_task":
            if payload_obj and payload_obj.get("head_sha"):
                return str(payload_obj.get("head_sha"))
            return None
        if source_type == "jira" and task_type == "jira_workflow_review_task" and request.issue_key:
            return f"{request.issue_key}:{request.trigger_status}" if request.trigger_status else request.issue_key
        return None

    def _evaluate_capability_profile_event_gate(self, *, agent, source_type: str, event_type: str, db: Session, capability_context=None) -> dict | None:
        if not agent:
            return None
        context = capability_context
        if context is None:
            profile_id, resolved_profile = self.capability_context_service.resolve_for_agent(db, agent)
            context = self.capability_context_service.build_runtime_capability_context(profile_id, resolved_profile, db=db, agent_id=agent.id)
        if isinstance(context, dict):
            allowed_external_systems = context.get("allowed_external_systems", [])
            allowed_webhook_triggers = context.get("allowed_webhook_triggers", [])
        else:
            allowed_external_systems = getattr(context, "allowed_external_systems", []) or []
            allowed_webhook_triggers = getattr(context, "allowed_webhook_triggers", []) or []

        if allowed_external_systems and source_type not in allowed_external_systems:
            return {"routing_reason": "external_system_not_allowed", "message": "Matched agent capability profile does not allow this source_type"}
        if allowed_webhook_triggers and event_type not in allowed_webhook_triggers:
            return {"routing_reason": "webhook_trigger_not_allowed", "message": "Matched agent capability profile does not allow this event_type"}
        return None

    def route_external_event(self, request: ExternalEventIngressRequest, db: Session) -> ExternalEventIngressResponse:
        source_type = self._normalize_source_type(request.source_type)
        task_repo = AgentTaskRepository(db)
        workflow_rule_repo = WorkflowTransitionRuleRepository(db)
        agent_repo = AgentRepository(db)

        if source_type == "github" and request.event_type == "pull_request_review_requested":
            return self._route_github_pr_review_requested_via_automation_rule(request, db)

        if source_type == "jira" and request.event_type == "workflow_review_requested":
            if not request.project_key or not request.issue_type or not request.trigger_status:
                return ExternalEventIngressResponse(accepted=False, matched_subscription_ids=[], routing_reason="missing_jira_workflow_context", message="project_key, issue_type, and trigger_status are required for jira workflow routing")

            matched_rule = workflow_rule_repo.find_matching_jira_rule(
                project_key=request.project_key,
                issue_type=request.issue_type,
                trigger_status=request.trigger_status,
                assignee_binding=request.issue_assignee,
            )
            if not matched_rule:
                return ExternalEventIngressResponse(accepted=False, matched_subscription_ids=[], routing_reason="no_matching_workflow_rule", message="No enabled jira workflow transition rule matched the event context")

            matched_agent = agent_repo.get_by_id(matched_rule.target_agent_id)
            gate_rejection = self._evaluate_capability_profile_event_gate(agent=matched_agent, source_type=source_type, event_type=request.event_type, db=db)
            if gate_rejection:
                return ExternalEventIngressResponse(
                    accepted=False,
                    matched_subscription_ids=[],
                    routing_reason=gate_rejection["routing_reason"],
                    matched_agent_id=matched_rule.target_agent_id,
                    matched_workflow_rule_id=matched_rule.id,
                    resolved_task_type="jira_workflow_review_task",
                    message=gate_rejection["message"],
                )

            _cfg, parsed_workflow_context, config_error = parse_workflow_rule_config(matched_rule.config_json)
            if config_error:
                return ExternalEventIngressResponse(accepted=False, matched_subscription_ids=[], routing_reason="invalid_workflow_rule_config", matched_workflow_rule_id=matched_rule.id, resolved_task_type="jira_workflow_review_task", message="Matched workflow rule has invalid config_json")

            input_payload_json = json.dumps(
                {
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
            )
            matched_agent_id = matched_rule.target_agent_id
            task_type = "jira_workflow_review_task"
            routing_reason = "matched_workflow_rule"
            matched_workflow_rule_id = matched_rule.id
        elif self._is_removed_legacy_provider_automation_event(source_type, request.event_type):
            # New automation triggers must be modeled as AutomationRule.
            # RuntimeProfile must not contain provider.automation.
            return ExternalEventIngressResponse(
                accepted=False,
                matched_subscription_ids=[],
                routing_reason="legacy_provider_automation_removed",
                message="Legacy runtime_profile provider.automation routing has been removed. Create a first-class AutomationRule for this trigger type.",
            )
        else:
            return ExternalEventIngressResponse(
                accepted=False,
                matched_subscription_ids=[],
                routing_reason="unsupported_automation_event",
                message="No automation rule configured for this source_type/event_type",
            )

        payload_obj = self._parse_json_object(input_payload_json)
        if source_type == "jira":
            dedupe_hint = request.dedupe_key or request.issue_key or request.target_ref
            shared_context_ref = request.issue_key or request.dedupe_key or request.target_ref
        else:
            dedupe_hint = request.dedupe_key
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
                return ExternalEventIngressResponse(accepted=True, matched_subscription_ids=[], routing_reason="duplicate_task", matched_agent_id=matched_agent_id, created_task_id=duplicate.id, matched_workflow_rule_id=matched_workflow_rule_id, resolved_task_type=task_type, deduped=True, message="Duplicate event detected; existing task reused")

        bundle_id = self._build_bundle_id(request=request, source_type=source_type, payload_obj=payload_obj, task_type=task_type)
        version_key = self._build_version_key(request=request, source_type=source_type, payload_obj=payload_obj, task_type=task_type)

        task = task_repo.create(
            parent_agent_id=None,
            assignee_agent_id=matched_agent_id,
            owner_user_id=matched_agent.owner_user_id if matched_agent else None,
            created_by_user_id=None,
            source=source_type,
            task_type=task_type,
            input_payload_json=input_payload_json,
            shared_context_ref=shared_context_ref,
            task_family="triggered_work",
            provider=source_type,
            trigger=request.event_type,
            bundle_id=bundle_id,
            version_key=version_key,
            dedupe_key=dedupe_hint,
            status="queued",
            result_payload_json=None,
            retry_count=0,
        )

        should_dispatch = task_type in {"jira_workflow_review_task", "github_review_task", "triggered_event_task"}
        if should_dispatch:
            self._dispatch_task_in_background(task.id)
            message = "Event routed, task created, and scheduled for background dispatch"
        else:
            message = "Event routed and task created"

        return ExternalEventIngressResponse(
            accepted=True,
            matched_subscription_ids=[],
            routing_reason=routing_reason,
            matched_agent_id=matched_agent_id,
            created_task_id=task.id,
            matched_workflow_rule_id=matched_workflow_rule_id,
            resolved_task_type=task_type,
            deduped=False,
            message=message,
        )

    def _dispatch_task_in_background(self, task_id: str) -> None:
        self.task_dispatcher.dispatch_task_in_background(task_id)
