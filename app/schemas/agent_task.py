from datetime import datetime

from pydantic import BaseModel, Field
from typing import Any, Optional


class AgentTaskCreateRequest(BaseModel):
    group_id: Optional[str] = None
    parent_agent_id: Optional[str] = None
    assignee_agent_id: str
    source: str
    task_type: str
    template_id: Optional[str] = None
    input_payload_json: Optional[str] = None
    shared_context_ref: Optional[str] = None
    task_family: Optional[str] = None
    provider: Optional[str] = None
    trigger: Optional[str] = None
    bundle_id: Optional[str] = None
    version_key: Optional[str] = None
    dedupe_key: Optional[str] = None
    status: str = "queued"
    result_payload_json: Optional[str] = None
    retry_count: int = 0


class CreateTaskFromTemplateRequest(BaseModel):
    template_id: str
    assignee_agent_id: str
    dispatch_immediately: bool = True
    input: dict[str, Any] = Field(default_factory=dict)
    group_id: Optional[str] = None
    parent_agent_id: Optional[str] = None


class TaskTemplateRead(BaseModel):
    template_id: str
    label: str
    description: str
    task_type: str
    task_family: str
    provider: Optional[str] = None
    default_trigger: Optional[str] = None
    default_skill_name: Optional[str] = None
    required_inputs: tuple[str, ...] = ()
    optional_inputs: tuple[str, ...] = ()
    output_artifacts: tuple[str, ...] = ()
    compatible_bundle_templates: tuple[str, ...] = ()
    requires_bundle: bool = False
    requires_sources: bool = False
    dispatch_immediately_default: bool = True


class AgentTaskResponse(BaseModel):
    id: str
    group_id: Optional[str] = None
    parent_agent_id: Optional[str] = None
    assignee_agent_id: str
    source: str
    task_type: str
    template_id: Optional[str] = None
    input_payload_json: Optional[str] = None
    shared_context_ref: Optional[str] = None
    task_family: Optional[str] = None
    provider: Optional[str] = None
    trigger: Optional[str] = None
    bundle_id: Optional[str] = None
    version_key: Optional[str] = None
    dedupe_key: Optional[str] = None
    status: str
    owner_user_id: Optional[int] = None
    created_by_user_id: Optional[int] = None
    runtime_request_id: Optional[str] = None
    summary: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    result_payload_json: Optional[str] = None
    retry_count: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
