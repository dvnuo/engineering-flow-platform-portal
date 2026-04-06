import json

from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.external_event_subscription_repo import ExternalEventSubscriptionRepository
from app.repositories.workflow_transition_rule_repo import WorkflowTransitionRuleRepository
from app.schemas.external_event_ingress import ExternalEventIngressRequest, ExternalEventIngressResponse
from app.services.runtime_router import RuntimeRouterService
from sqlalchemy.orm import Session


class ExternalEventRouterService:
    def __init__(self) -> None:
        self.runtime_router = RuntimeRouterService()

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
                "workflow_context": matched_rule.config_json,
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

            routing_decision = self.runtime_router.resolve_binding_decision(
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
            task_type = self._derive_task_type(source_type, request.event_type)
            input_payload_json = request.payload_json
            routing_reason = routing_decision.reason

        if source_type == "jira":
            shared_context_ref = request.issue_key or request.dedupe_key or request.target_ref
        else:
            shared_context_ref = request.dedupe_key or request.target_ref

        if request.dedupe_key:
            duplicate = task_repo.find_recent_duplicate(
                assignee_agent_id=matched_agent_id,
                source=source_type,
                task_type=task_type,
                dedupe_hint=request.dedupe_key,
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
