import json

from sqlalchemy.orm import Session

from app.repositories.agent_identity_binding_repo import AgentIdentityBindingRepository
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.repositories.workflow_transition_rule_repo import WorkflowTransitionRuleRepository
from app.schemas.external_event_ingress import ExternalEventIngressRequest, ExternalEventIngressResponse
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
    def _binding_lookup_username_from_metadata(request: ExternalEventIngressRequest) -> str | None:
        metadata = ExternalEventRouterService._parse_metadata_object(request.metadata_json)
        if not metadata:
            return None
        username = str(metadata.get("binding_lookup_username") or "").strip()
        return username or None

    @staticmethod
    def _automation_rule_for_event(source_type: str, event_type: str) -> dict | None:
        rules = {
            ("github", "pull_request_review_requested"): {
                "task_type": "github_review_task",
                "provider": "github",
                "automation_path": ("review_requests",),
                "scope_key": "repos",
                "dispatch": True,
                "source_kind": "github.pull_request_review_requested",
                "automation_rule": "github.review_requests",
            },
            ("github", "mention"): {
                "task_type": "triggered_event_task",
                "provider": "github",
                "automation_path": ("mentions",),
                "scope_key": "repos",
                "dispatch": True,
                "source_kind": "github.mention",
                "automation_rule": "github.mentions",
            },
            ("jira", "assigned"): {
                "task_type": "triggered_event_task",
                "provider": "jira",
                "automation_path": ("assignments",),
                "scope_key": "projects",
                "dispatch": True,
                "source_kind": "jira.assigned",
                "automation_rule": "jira.assignments",
            },
            ("jira", "mention"): {
                "task_type": "triggered_event_task",
                "provider": "jira",
                "automation_path": ("mentions",),
                "scope_key": "projects",
                "dispatch": True,
                "source_kind": "jira.mention",
                "automation_rule": "jira.mentions",
            },
            ("confluence", "mention"): {
                "task_type": "triggered_event_task",
                "provider": "confluence",
                "automation_path": ("mentions",),
                "scope_key": "spaces",
                "dispatch": True,
                "source_kind": "confluence.mention",
                "automation_rule": "confluence.mentions",
            },
        }
        return rules.get((source_type, event_type))

    @staticmethod
    def _parse_event_payload_object(request: ExternalEventIngressRequest) -> dict | None:
        return ExternalEventRouterService._parse_json_object(request.payload_json)

    def _build_triggered_event_input_payload(
        self,
        request: ExternalEventIngressRequest,
        rule: dict,
        binding,
    ) -> tuple[dict | None, str | None]:
        payload_obj = self._parse_event_payload_object(request)
        if not payload_obj:
            return None, "payload_json must be a non-empty JSON object for triggered_event_task routing"

        source_type = self._normalize_source_type(request.source_type)
        merged_payload = dict(payload_obj)
        merged_payload["source_kind"] = rule["source_kind"]
        merged_payload["source_type"] = source_type
        merged_payload["event_type"] = request.event_type
        merged_payload["external_account_id"] = request.external_account_id
        merged_payload["binding_id"] = binding.id
        merged_payload["automation_rule"] = rule["automation_rule"]

        metadata_obj = self._parse_metadata_object(request.metadata_json)
        if isinstance(metadata_obj, dict) and metadata_obj.get("trigger_mode"):
            merged_payload["trigger_mode"] = metadata_obj.get("trigger_mode")

        source_kind = rule["source_kind"]
        if source_kind == "github.mention":
            if not merged_payload.get("owner"):
                return None, "github mention payload requires owner"
            if not merged_payload.get("repo"):
                return None, "github mention payload requires repo"
            if merged_payload.get("issue_number") is None and merged_payload.get("pull_number") is None:
                return None, "github mention payload requires issue_number or pull_number"
        elif source_kind == "jira.assigned":
            if not merged_payload.get("issue_key"):
                return None, "jira assigned payload requires issue_key"
            if not merged_payload.get("project_key"):
                return None, "jira assigned payload requires project_key"
        elif source_kind == "jira.mention":
            if not merged_payload.get("issue_key"):
                return None, "jira mention payload requires issue_key"
            if not merged_payload.get("project_key"):
                return None, "jira mention payload requires project_key"
        elif source_kind == "confluence.mention":
            if not merged_payload.get("page_id"):
                return None, "confluence mention payload requires page_id"
            if not merged_payload.get("space_key") and not merged_payload.get("space"):
                return None, "confluence mention payload requires space_key or space"

        return merged_payload, None

    @staticmethod
    def _load_runtime_profile_config_for_agent(*, db: Session, agent) -> dict:
        if not agent or not agent.runtime_profile_id:
            return {}
        profile = RuntimeProfileRepository(db).get_by_id(agent.runtime_profile_id)
        if not profile:
            return {}
        return ExternalEventRouterService._parse_json_object(profile.config_json) or {}

    @staticmethod
    def _automation_enabled_for_config(config: dict, source_type: str, event_type: str) -> bool:
        rule = ExternalEventRouterService._automation_rule_for_event(source_type, event_type)
        if not rule:
            return False
        provider_cfg = config.get(rule["provider"]) if isinstance(config, dict) else None
        if not isinstance(provider_cfg, dict) or not provider_cfg.get("enabled"):
            return False
        automation_cfg = provider_cfg.get("automation")
        if not isinstance(automation_cfg, dict):
            return False
        target = automation_cfg
        for key in rule["automation_path"]:
            target = target.get(key) if isinstance(target, dict) else None
        return isinstance(target, dict) and bool(target.get("enabled"))

    @staticmethod
    def _effective_scope_for_binding_and_config(binding, config: dict, source_type: str, event_type: str) -> list[str]:
        rule = ExternalEventRouterService._automation_rule_for_event(source_type, event_type)
        if not rule:
            return []
        scope_key = rule["scope_key"]
        binding_scope_obj = ExternalEventRouterService._parse_json_object(getattr(binding, "scope_json", None)) or {}
        binding_scope = binding_scope_obj.get(scope_key)
        if isinstance(binding_scope, list):
            cleaned = [str(item).strip() for item in binding_scope if str(item).strip()]
            if cleaned:
                return cleaned

        provider_cfg = config.get(rule["provider"]) if isinstance(config, dict) else {}
        automation_cfg = provider_cfg.get("automation") if isinstance(provider_cfg, dict) else {}
        target = automation_cfg
        for key in rule["automation_path"]:
            target = target.get(key) if isinstance(target, dict) else {}
        configured_scope = target.get(scope_key) if isinstance(target, dict) else []
        if isinstance(configured_scope, list):
            return [str(item).strip() for item in configured_scope if str(item).strip()]
        return []

    @staticmethod
    def _event_scope_value(event: ExternalEventIngressRequest, source_type: str, payload_obj: dict | None) -> str | None:
        if source_type == "github":
            if payload_obj and payload_obj.get("owner") and payload_obj.get("repo"):
                return f"{payload_obj.get('owner')}/{payload_obj.get('repo')}"
            if event.target_ref and "/" in event.target_ref:
                return event.target_ref.strip()
            return None
        if source_type == "jira":
            if event.project_key:
                return event.project_key.strip()
            if event.target_ref:
                return event.target_ref.strip()
            if event.issue_key and "-" in event.issue_key:
                return event.issue_key.split("-", 1)[0]
            if payload_obj and payload_obj.get("project_key"):
                return str(payload_obj.get("project_key")).strip()
            return None
        if source_type == "confluence":
            if event.target_ref:
                return event.target_ref.strip()
            if payload_obj and payload_obj.get("space"):
                return str(payload_obj.get("space")).strip()
            if payload_obj and payload_obj.get("space_key"):
                return str(payload_obj.get("space_key")).strip()
            return None
        return None

    @staticmethod
    def _is_event_in_effective_scope(event: ExternalEventIngressRequest, source_type: str, event_type: str, effective_scope: list[str]) -> bool:
        _ = event_type
        if not effective_scope:
            return True
        payload_obj = ExternalEventRouterService._parse_json_object(event.payload_json)
        value = ExternalEventRouterService._event_scope_value(event, source_type, payload_obj)
        if not value:
            return False
        return value in set(effective_scope)

    @staticmethod
    def _extract_github_review_payload(request: ExternalEventIngressRequest) -> tuple[dict | None, str | None]:
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

    def _stale_superseded_github_review_tasks_for_bundle(self, *, task_repo: AgentTaskRepository, assignee_agent_id: str, bundle_id: str | None, new_version_key: str | None, superseding_task_id: str) -> None:
        if not bundle_id:
            return
        candidates = task_repo.list_active_tasks_for_bundle(
            assignee_agent_id=assignee_agent_id,
            bundle_id=bundle_id,
            task_type="github_review_task",
        )
        stale_payload = self._build_github_superseded_payload(
            superseding_task_id=superseding_task_id,
            new_head_sha=new_version_key,
        )
        to_update = []
        for task in candidates:
            if task.id == superseding_task_id:
                continue
            if new_version_key and task.version_key == new_version_key:
                continue
            task.status = "stale"
            task.result_payload_json = stale_payload
            to_update.append(task)
        task_repo.save_all(to_update)

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
        else:
            rule = self._automation_rule_for_event(source_type, request.event_type)
            if not rule:
                return ExternalEventIngressResponse(accepted=False, matched_subscription_ids=[], routing_reason="unsupported_automation_event", message="No automation rule configured for this source_type/event_type")
            if not request.external_account_id:
                return ExternalEventIngressResponse(accepted=False, matched_subscription_ids=[], routing_reason="missing_external_account_id", message="external_account_id is required for identity binding based routing")

            binding_repo = AgentIdentityBindingRepository(db)
            bindings = binding_repo.list_bindings_for_key(system_type=source_type, external_account_id=request.external_account_id)
            if not bindings:
                lookup_username = self._binding_lookup_username_from_metadata(request)
                if lookup_username:
                    bindings = binding_repo.list_bindings_for_username(system_type=source_type, username=lookup_username)
            if not bindings:
                return ExternalEventIngressResponse(accepted=False, matched_subscription_ids=[], routing_reason="no_enabled_binding", message="No agent matched the provided identity binding")

            selected_binding = None
            matched_agent = None
            routing_reason = "matched_enabled_binding"
            for binding in bindings:
                candidate_agent = agent_repo.get_by_id(binding.agent_id)
                if not candidate_agent:
                    continue
                routing_decision = self.runtime_router.resolve_agent_decision_for_event(agent_id=binding.agent_id, db=db, reason="matched_enabled_binding")
                gate_rejection = self._evaluate_capability_profile_event_gate(
                    agent=candidate_agent,
                    source_type=source_type,
                    event_type=request.event_type,
                    db=db,
                    capability_context=routing_decision.capability_context,
                )
                if gate_rejection:
                    continue
                config = self._load_runtime_profile_config_for_agent(db=db, agent=candidate_agent)
                if not self._automation_enabled_for_config(config, source_type, request.event_type):
                    continue
                effective_scope = self._effective_scope_for_binding_and_config(binding, config, source_type, request.event_type)
                if not self._is_event_in_effective_scope(request, source_type, request.event_type, effective_scope):
                    continue
                selected_binding = binding
                matched_agent = candidate_agent
                routing_reason = routing_decision.reason
                break

            if not selected_binding or not matched_agent:
                return ExternalEventIngressResponse(accepted=False, matched_subscription_ids=[], routing_reason="automation_not_enabled_or_scope_mismatch", message="No bound agent has enabled automation for this event in effective scope")

            matched_agent_id = selected_binding.agent_id
            matched_workflow_rule_id = None
            task_type = rule["task_type"]

            if source_type == "github" and request.event_type == "pull_request_review_requested":
                github_payload, github_error = self._extract_github_review_payload(request)
                if github_error:
                    return ExternalEventIngressResponse(accepted=False, matched_subscription_ids=[], routing_reason="invalid_github_event_payload", matched_agent_id=matched_agent_id, resolved_task_type=task_type, message=github_error)
                input_payload_json = json.dumps(github_payload)
            else:
                triggered_payload, triggered_payload_error = self._build_triggered_event_input_payload(
                    request=request,
                    rule=rule,
                    binding=selected_binding,
                )
                if triggered_payload_error:
                    return ExternalEventIngressResponse(
                        accepted=False,
                        matched_subscription_ids=[],
                        routing_reason="invalid_triggered_event_payload",
                        matched_agent_id=matched_agent_id,
                        resolved_task_type=task_type,
                        message=triggered_payload_error,
                    )
                input_payload_json = json.dumps(triggered_payload, ensure_ascii=False)

        payload_obj = self._parse_json_object(input_payload_json)
        if source_type == "jira":
            dedupe_hint = request.dedupe_key or request.issue_key or request.target_ref
            shared_context_ref = request.issue_key or request.dedupe_key or request.target_ref
        else:
            dedupe_hint = self._build_github_dedupe_hint(request, payload_obj) or request.dedupe_key
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

        if source_type == "github" and request.event_type == "pull_request_review_requested":
            self._stale_superseded_github_review_tasks_for_bundle(
                task_repo=task_repo,
                assignee_agent_id=matched_agent_id,
                bundle_id=bundle_id,
                new_version_key=version_key,
                superseding_task_id=task.id,
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
