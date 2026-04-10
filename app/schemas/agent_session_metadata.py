from datetime import datetime
from pydantic import BaseModel
from typing import Optional


class AgentSessionMetadataUpsertRequest(BaseModel):
    group_id: Optional[str] = None
    current_task_id: Optional[str] = None
    current_delegation_id: Optional[str] = None
    current_coordination_run_id: Optional[str] = None
    source_type: Optional[str] = None
    source_ref: Optional[str] = None
    last_execution_id: Optional[str] = None
    latest_event_type: Optional[str] = None
    latest_event_state: Optional[str] = None
    snapshot_version: Optional[str] = None
    pending_delegations_json: Optional[str] = None
    runtime_events_json: Optional[str] = None
    metadata_json: Optional[str] = None


class AgentSessionMetadataResponse(BaseModel):
    id: str
    session_id: str
    agent_id: str
    group_id: Optional[str] = None
    current_task_id: Optional[str] = None
    current_delegation_id: Optional[str] = None
    current_coordination_run_id: Optional[str] = None
    source_type: Optional[str] = None
    source_ref: Optional[str] = None
    last_execution_id: Optional[str] = None
    latest_event_type: Optional[str] = None
    latest_event_state: Optional[str] = None
    snapshot_version: Optional[str] = None
    pending_delegations_json: Optional[str] = None
    runtime_events_json: Optional[str] = None
    metadata_json: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

