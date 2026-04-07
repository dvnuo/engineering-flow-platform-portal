from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class AgentGroupMemberCreateRequest(BaseModel):
    member_type: str
    user_id: int | None = None
    agent_id: str | None = None
    role: str = "member"


class AgentGroupMemberResponse(BaseModel):
    id: str
    group_id: str
    member_type: str
    user_id: int | None = None
    agent_id: str | None = None
    role: str
    created_at: datetime

    class Config:
        from_attributes = True


class AgentGroupCreateRequest(BaseModel):
    name: str
    leader_agent_id: str
    shared_context_policy_json: str | None = None
    task_routing_policy_json: str | None = None
    ephemeral_agent_policy_json: str | None = None
    member_user_ids: list[int] = Field(default_factory=list)
    member_agent_ids: list[str] = Field(default_factory=list)
    specialist_agent_ids: list[str] | None = None


class AgentGroupResponse(BaseModel):
    id: str
    name: str
    leader_agent_id: str
    shared_context_policy_json: str | None = None
    task_routing_policy_json: str | None = None
    ephemeral_agent_policy_json: str | None = None
    specialist_agent_pool_json: str | None = None
    created_by_user_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AgentGroupDetailResponse(AgentGroupResponse):
    members: list[AgentGroupMemberResponse]


class AgentGroupTaskCreateRequest(BaseModel):
    parent_agent_id: str | None = None
    assignee_agent_id: str
    source: str
    task_type: str
    input_payload_json: str | None = None
    shared_context_ref: str | None = None
    status: str = "queued"
    result_payload_json: str | None = None
    retry_count: int = 0


class AgentGroupTaskSummaryResponse(BaseModel):
    group_id: str
    total: int
    queued: int
    running: int
    done: int
    failed: int


class AgentGroupSpecialistPoolResponse(BaseModel):
    group_id: str
    specialist_agent_ids: list[str]


class InternalAgentGroupSpecialistPoolItemResponse(BaseModel):
    agent_id: str
    name: str
    agent_type: str
    status: str
    visibility: str


class InternalAgentGroupSpecialistPoolResponse(BaseModel):
    group_id: str
    specialist_agent_ids: list[str]
    items: list[InternalAgentGroupSpecialistPoolItemResponse]


class AgentGroupSpecialistPoolUpdateRequest(BaseModel):
    specialist_agent_ids: list[str] = Field(default_factory=list)


class AgentGroupTaskAgentCreateRequest(BaseModel):
    name: str
    template_agent_id: str
    scope_label: str | None = None
    cleanup_policy: str | None = None


class InternalAgentGroupTaskAgentCreateRequest(BaseModel):
    leader_agent_id: str
    template_agent_id: str
    name: str
    scope_label: str | None = None
    visibility: str | None = None
    task_agent_cleanup_policy: str | None = None

    @field_validator("leader_agent_id")
    @classmethod
    def validate_leader_agent_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("leader_agent_id must not be blank")
        return normalized

    @field_validator("visibility")
    @classmethod
    def validate_visibility(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip().lower()
        allowed = {"private", "shared", "public"}
        if normalized not in allowed:
            raise ValueError("visibility must be one of: private, shared, public")
        return normalized

    @field_validator("task_agent_cleanup_policy")
    @classmethod
    def validate_cleanup_policy(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip().lower()
        allowed = {"retain", "delete_on_done", "delete_on_terminal"}
        if normalized not in allowed:
            raise ValueError("task_agent_cleanup_policy must be one of: retain, delete_on_done, delete_on_terminal")
        return normalized


class InternalAgentGroupTaskAgentCreateResponse(BaseModel):
    id: str
    name: str
    agent_type: str
    status: str
    visibility: str
    template_agent_id: str
    scope_label: str | None = None
    task_agent_cleanup_policy: str | None = None
    source: str
    group_id: str
    leader_agent_id: str
