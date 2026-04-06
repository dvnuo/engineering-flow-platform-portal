from datetime import datetime

from pydantic import BaseModel
from typing import Optional


class AgentTaskCreateRequest(BaseModel):
    group_id: Optional[str] = None
    parent_agent_id: Optional[str] = None
    assignee_agent_id: str
    source: str
    task_type: str
    input_payload_json: Optional[str] = None
    shared_context_ref: Optional[str] = None
    status: str = "queued"
    result_payload_json: Optional[str] = None
    retry_count: int = 0


class AgentTaskResponse(BaseModel):
    id: str
    group_id: Optional[str] = None
    parent_agent_id: Optional[str] = None
    assignee_agent_id: str
    source: str
    task_type: str
    input_payload_json: Optional[str] = None
    shared_context_ref: Optional[str] = None
    status: str
    result_payload_json: Optional[str] = None
    retry_count: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
