from datetime import datetime

from pydantic import BaseModel, field_validator


ALLOWED_VISIBILITY = {"leader_only", "group_visible"}


class AgentDelegationCreateRequest(BaseModel):
    group_id: str
    parent_agent_id: str | None = None
    leader_agent_id: str
    assignee_agent_id: str
    objective: str
    scoped_context_ref: str | None = None
    input_artifacts_json: str | None = None
    expected_output_schema_json: str | None = None
    deadline_at: datetime | None = None
    retry_policy_json: str | None = None
    visibility: str = "leader_only"
    skill_name: str
    skill_kwargs_json: str | None = None

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


class AgentDelegationResponse(BaseModel):
    id: str
    group_id: str
    parent_agent_id: str | None = None
    leader_agent_id: str
    assignee_agent_id: str
    agent_task_id: str | None = None
    objective: str
    scoped_context_ref: str | None = None
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


class AgentGroupTaskBoardResponse(BaseModel):
    group_id: str
    leader_agent_id: str | None = None
    summary: AgentDelegationBoardSummaryResponse
    items: list[AgentDelegationBoardItemResponse]
