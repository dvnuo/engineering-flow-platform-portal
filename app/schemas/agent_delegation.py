import json
from datetime import datetime

from pydantic import BaseModel, field_validator


ALLOWED_VISIBILITY = {"leader_only", "group_visible"}


class AgentDelegationCreateRequest(BaseModel):
    group_id: str
    parent_agent_id: str | None = None
    leader_agent_id: str
    assignee_agent_id: str
    objective: str
    leader_session_id: str | None = None
    scoped_context_ref: str | None = None
    scoped_context_payload_json: str | None = None
    input_artifacts_json: str | None = None
    expected_output_schema_json: str | None = None
    deadline_at: datetime | None = None
    retry_policy_json: str | None = None
    visibility: str = "leader_only"
    skill_name: str
    skill_kwargs_json: str | None = None


    @field_validator("scoped_context_payload_json")
    @classmethod
    def validate_scoped_context_payload_json(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        try:
            parsed = json.loads(value)
        except Exception as exc:
            raise ValueError("scoped_context_payload_json must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("scoped_context_payload_json must decode to a JSON object")
        return value

    @field_validator("visibility")
    @classmethod
    def validate_visibility(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in ALLOWED_VISIBILITY:
            raise ValueError("visibility must be 'leader_only' or 'group_visible'")
        return normalized

    @field_validator("skill_name")
    @classmethod
    def validate_skill_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("skill_name is required")
        return value.strip()

class InternalAgentDelegationCreateRequest(BaseModel):
    group_id: str
    parent_agent_id: str | None = None
    leader_agent_id: str
    assignee_agent_id: str
    objective: str
    leader_session_id: str | None = None
    scoped_context_ref: str | None = None
    scoped_context_payload: dict | None = None
    input_artifacts: list[dict] | None = None
    expected_output_schema: dict | None = None
    deadline_at: datetime | None = None
    retry_policy: dict | None = None
    visibility: str = "leader_only"
    skill_name: str
    skill_kwargs: dict | None = None
    coordination_run_id: str | None = None
    round_index: int | None = 1

    @field_validator("visibility")
    @classmethod
    def validate_visibility(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in ALLOWED_VISIBILITY:
            raise ValueError("visibility must be 'leader_only' or 'group_visible'")
        return normalized

    @field_validator("skill_name")
    @classmethod
    def validate_skill_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("skill_name is required")
        return value.strip()

    @field_validator("round_index")
    @classmethod
    def validate_round_index(cls, value: int | None) -> int | None:
        if value is None:
            return value
        if value < 1:
            raise ValueError("round_index must be >= 1")
        return value


class AgentDelegationResponse(BaseModel):
    id: str
    group_id: str
    parent_agent_id: str | None = None
    leader_agent_id: str
    assignee_agent_id: str
    agent_task_id: str | None = None
    objective: str
    leader_session_id: str | None = None
    origin_session_id: str | None = None
    reply_target_type: str
    coordination_run_id: str | None = None
    round_index: int
    scoped_context_ref: str | None = None
    scoped_context_payload_json: str | None = None
    input_artifacts_json: str | None = None
    expected_output_schema_json: str | None = None
    deadline_at: datetime | None = None
    retry_policy_json: str | None = None
    visibility: str
    status: str
    result_summary: str | None = None
    result_artifacts_json: str | None = None
    blockers_json: str | None = None
    next_recommendation: str | None = None
    audit_trace_json: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AgentDelegationBoardItemResponse(BaseModel):
    id: str
    group_id: str
    leader_agent_id: str
    assignee_agent_id: str
    objective: str
    leader_session_id: str | None = None
    origin_session_id: str | None = None
    reply_target_type: str
    coordination_run_id: str | None = None
    round_index: int
    visibility: str
    status: str
    agent_task_id: str | None = None
    result_summary: str | None = None
    next_recommendation: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AgentDelegationBoardSummaryResponse(BaseModel):
    total: int
    queued: int
    running: int
    done: int
    failed: int


class AgentDelegationRunSummaryResponse(BaseModel):
    coordination_run_id: str
    total: int
    queued: int
    running: int
    done: int
    failed: int
    latest_round_index: int


class AgentGroupTaskBoardResponse(BaseModel):
    group_id: str
    leader_agent_id: str | None = None
    summary: AgentDelegationBoardSummaryResponse
    items: list[AgentDelegationBoardItemResponse]
    runs: list[AgentDelegationRunSummaryResponse] = []
