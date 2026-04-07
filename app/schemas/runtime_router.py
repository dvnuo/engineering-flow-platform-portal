from pydantic import BaseModel, Field
from typing import Optional


class ResolveBindingRequest(BaseModel):
    system_type: str
    external_account_id: str


class RuntimeTargetInfoResponse(BaseModel):
    agent_id: str
    namespace: Optional[str] = None
    service_name: Optional[str] = None
    endpoint_path: Optional[str] = None


class RuntimeCapabilityContextResponse(BaseModel):
    capability_profile_id: Optional[str] = None
    tool_set: list[str] = Field(default_factory=list)
    channel_set: list[str] = Field(default_factory=list)
    skill_set: list[str] = Field(default_factory=list)
    allowed_capability_ids: list[str] = Field(default_factory=list)
    allowed_capability_types: list[str] = Field(default_factory=list)
    allowed_external_systems: list[str] = Field(default_factory=list)
    allowed_webhook_triggers: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    allowed_adapter_actions: list[str] = Field(default_factory=list)


class RuntimeRoutingDecisionResponse(BaseModel):
    matched_agent_id: Optional[str] = None
    matched_agent_type: Optional[str] = None
    policy_profile_id: Optional[str] = None
    capability_profile_id: Optional[str] = None
    reason: str
    execution_mode: str = "sync"
    runtime_target: Optional[RuntimeTargetInfoResponse] = None
    capability_context: Optional[RuntimeCapabilityContextResponse] = None


class AgentRuntimeContextResponse(BaseModel):
    agent_id: str
    agent_type: str
    capability_profile_id: Optional[str] = None
    policy_profile_id: Optional[str] = None
    capability_context: RuntimeCapabilityContextResponse
    runtime_target: RuntimeTargetInfoResponse
