from datetime import datetime

from pydantic import BaseModel
from typing import Optional


class WorkflowTransitionRuleCreateRequest(BaseModel):
    system_type: str
    project_key: str
    issue_type: str
    trigger_status: str
    assignee_binding: Optional[str] = None
    target_agent_id: str
    skill_name: Optional[str] = None
    success_transition: Optional[str] = None
    failure_transition: Optional[str] = None
    success_reassign_to: Optional[str] = None
    failure_reassign_to: Optional[str] = None
    explicit_success_assignee: Optional[str] = None
    explicit_failure_assignee: Optional[str] = None
    enabled: bool = True
    config_json: Optional[str] = None


class WorkflowTransitionRuleResponse(BaseModel):
    id: str
    system_type: str
    project_key: str
    issue_type: str
    trigger_status: str
    assignee_binding: Optional[str] = None
    target_agent_id: str
    skill_name: Optional[str] = None
    success_transition: Optional[str] = None
    failure_transition: Optional[str] = None
    success_reassign_to: Optional[str] = None
    failure_reassign_to: Optional[str] = None
    explicit_success_assignee: Optional[str] = None
    explicit_failure_assignee: Optional[str] = None
    enabled: bool
    config_json: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
