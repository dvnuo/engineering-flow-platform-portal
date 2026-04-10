from pydantic import BaseModel
from typing import Optional


class ExternalEventIngressRequest(BaseModel):
    source_type: str
    event_type: str
    external_account_id: Optional[str] = None
    target_ref: Optional[str] = None
    dedupe_key: Optional[str] = None
    payload_json: Optional[str] = None
    metadata_json: Optional[str] = None
    project_key: Optional[str] = None
    issue_type: Optional[str] = None
    trigger_status: Optional[str] = None
    issue_key: Optional[str] = None
    issue_assignee: Optional[str] = None


class ExternalEventIngressResponse(BaseModel):
    accepted: bool
    matched_subscription_ids: list[str]
    routing_reason: str
    matched_agent_id: Optional[str] = None
    created_task_id: Optional[str] = None
    matched_workflow_rule_id: Optional[str] = None
    resolved_task_type: Optional[str] = None
    deduped: bool = False
    message: str
