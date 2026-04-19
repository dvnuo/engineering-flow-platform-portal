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
    context_compaction_level: Optional[str] = None
    context_objective_preview: Optional[str] = None
    context_summary_preview: Optional[str] = None
    context_next_step_preview: Optional[str] = None
    context_usage_percent: Optional[float] = None
    context_estimated_tokens: Optional[int] = None
    context_window_tokens: Optional[int] = None
    context_next_compaction_action: Optional[str] = None
    context_tokens_until_soft_threshold: Optional[int] = None
    context_tokens_until_hard_threshold: Optional[int] = None
    active_skill_name: Optional[str] = None
    active_skill_status: Optional[str] = None
    active_skill_goal: Optional[str] = None
    active_skill_hash: Optional[str] = None
    active_skill_turn_count: Optional[int] = None
    active_skill_activation_reason: Optional[str] = None
    active_skill_tool_policy_declared: Optional[bool] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
