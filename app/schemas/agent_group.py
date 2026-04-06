from datetime import datetime

from pydantic import BaseModel, Field


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


class AgentGroupResponse(BaseModel):
    id: str
    name: str
    leader_agent_id: str
    shared_context_policy_json: str | None = None
    task_routing_policy_json: str | None = None
    ephemeral_agent_policy_json: str | None = None
    created_by_user_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AgentGroupDetailResponse(AgentGroupResponse):
    members: list[AgentGroupMemberResponse]
