from datetime import datetime

from pydantic import BaseModel
from typing import Optional


class AgentTaskCreateRequest(BaseModel):
    assignee_agent_id: str
    source: str
    task_type: str
    title: Optional[str] = None
    skill_name: Optional[str] = None
    parent_task_id: Optional[str] = None
    root_task_id: Optional[str] = None
    task_session_id: Optional[str] = None
    input_payload_json: Optional[str] = None
    task_family: Optional[str] = None
    provider: Optional[str] = None
    trigger: Optional[str] = None
    version_key: Optional[str] = None
    dedupe_key: Optional[str] = None
    status: str = "queued"
    result_payload_json: Optional[str] = None
    retry_count: int = 0


class CreateAgentAsyncTaskRequest(BaseModel):
    assignee_agent_id: str
    skill_name: str
    task_content: str


class CreateAgentTaskFollowupRequest(BaseModel):
    task_content: str


class AgentTaskResponse(BaseModel):
    id: str
    assignee_agent_id: str
    source: str
    task_type: str
    title: Optional[str] = None
    skill_name: Optional[str] = None
    parent_task_id: Optional[str] = None
    root_task_id: Optional[str] = None
    task_session_id: Optional[str] = None
    input_payload_json: Optional[str] = None
    task_family: Optional[str] = None
    provider: Optional[str] = None
    trigger: Optional[str] = None
    version_key: Optional[str] = None
    dedupe_key: Optional[str] = None
    status: str
    owner_user_id: Optional[int] = None
    created_by_user_id: Optional[int] = None
    owner_display_name: Optional[str] = None
    can_manage: bool = False
    assignee_agent_name: Optional[str] = None
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


class AgentTaskListItemResponse(BaseModel):
    id: str
    assignee_agent_id: str
    source: str
    task_type: str
    title: Optional[str] = None
    display_title: str
    skill_name: Optional[str] = None
    status: str
    owner_user_id: Optional[int] = None
    created_by_user_id: Optional[int] = None
    owner_display_name: Optional[str] = None
    can_manage: bool = False
    assignee_agent_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
