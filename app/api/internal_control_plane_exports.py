from fastapi import APIRouter, Depends, Query
import json
from sqlalchemy.orm import Session

from app.db import get_db
from app.repositories.agent_identity_binding_repo import AgentIdentityBindingRepository
from app.repositories.external_event_subscription_repo import ExternalEventSubscriptionRepository
from app.repositories.workflow_transition_rule_repo import WorkflowTransitionRuleRepository

router = APIRouter(tags=["internal-control-plane-exports"])


def _parse_config_json(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


@router.get("/api/internal/workflow-transition-rules")
def list_internal_workflow_transition_rules(
    system_type: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    project_key: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    rules = WorkflowTransitionRuleRepository(db).list_all()
    normalized_system = (system_type or "").strip().lower() or None
    normalized_project = (project_key or "").strip() or None
    if normalized_system:
        rules = [item for item in rules if (item.system_type or "").strip().lower() == normalized_system]
    if enabled is not None:
        rules = [item for item in rules if bool(item.enabled) is bool(enabled)]
    if normalized_project:
        rules = [item for item in rules if (item.project_key or "").strip() == normalized_project]

    data = []
    for item in rules:
        parsed_cfg = _parse_config_json(item.config_json)
        data.append(
            {
                "id": item.id,
                "system_type": item.system_type,
                "provider_type": item.system_type,
                "project_key": item.project_key,
                "project_keys": [item.project_key] if item.project_key else [],
                "issue_type": item.issue_type,
                "trigger_status": item.trigger_status,
                "trigger_statuses": [item.trigger_status] if item.trigger_status else [],
                "assignee_binding": item.assignee_binding,
                "target_agent_id": item.target_agent_id,
                "skill_name": item.skill_name,
                "success_transition": item.success_transition,
                "failure_transition": item.failure_transition,
                "success_reassign_to": item.success_reassign_to,
                "failure_reassign_to": item.failure_reassign_to,
                "explicit_success_assignee": item.explicit_success_assignee,
                "explicit_failure_assignee": item.explicit_failure_assignee,
                "review_comment_template": parsed_cfg.get("review_comment_template"),
                "transition_comment_template": parsed_cfg.get("transition_comment_template"),
                "config_json": item.config_json,
                "is_enabled": item.enabled,
                "enabled": item.enabled,
            }
        )
    return data


@router.get("/api/internal/agent-identity-bindings")
def list_internal_agent_identity_bindings(
    system_type: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    db: Session = Depends(get_db),
):
    normalized_system = (system_type or "").strip().lower()
    bindings = AgentIdentityBindingRepository(db).list_filtered(
        system_type=normalized_system,
        enabled=enabled,
    )

    return [
        {
            "id": item.id,
            "agent_id": item.agent_id,
            "system_type": item.system_type,
            "provider_type": item.system_type,
            "external_account_id": item.external_account_id,
            "username": item.username,
            "scope": item.scope_json,
            "scope_json": item.scope_json,
            "enabled": item.enabled,
        }
        for item in bindings
    ]


@router.get("/api/internal/external-event-subscriptions")
def list_internal_external_event_subscriptions(
    agent_id: str | None = Query(default=None),
    system_type: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    mode: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    normalized_system = (system_type or "").strip().lower() or None
    normalized_mode = (mode or "").strip().lower() or None

    subscriptions = ExternalEventSubscriptionRepository(db).list_filtered(
        agent_id=agent_id,
        source_type=normalized_system,
        enabled=enabled,
        mode=normalized_mode,
    )

    return [
        {
            "id": item.id,
            "agent_id": item.agent_id,
            "source_type": item.source_type,
            "provider_type": item.source_type,
            "event_type": item.event_type,
            "mode": item.mode,
            "source_kind": item.source_kind,
            "target_ref": item.target_ref,
            "binding_id": item.binding_id,
            "enabled": item.enabled,
            "dedupe_key_template": item.dedupe_key_template,
            "config_json": item.config_json,
            "config": _parse_config_json(item.config_json),
            "scope_json": item.scope_json,
            "scope": _parse_config_json(item.scope_json),
            "matcher_json": item.matcher_json,
            "matcher": _parse_config_json(item.matcher_json),
            "routing_json": item.routing_json,
            "routing": _parse_config_json(item.routing_json),
            "poll_profile_json": item.poll_profile_json,
            "poll_profile": _parse_config_json(item.poll_profile_json),
        }
        for item in subscriptions
    ]
