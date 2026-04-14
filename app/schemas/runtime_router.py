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
    unresolved_tools: list[str] = Field(default_factory=list)
    unresolved_skills: list[str] = Field(default_factory=list)
    unresolved_channels: list[str] = Field(default_factory=list)
    unresolved_actions: list[str] = Field(default_factory=list)
    resolved_action_mappings: dict[str, str] = Field(default_factory=dict)
    runtime_capability_catalog_version: Optional[str] = None
    runtime_capability_catalog_source: Optional[str] = None
    catalog_validation_mode: Optional[str] = None


class RuntimePolicyContextResponse(BaseModel):
    policy_profile_id: Optional[str] = None
    auto_run_rules: dict = Field(default_factory=dict)
    permission_rules: dict = Field(default_factory=dict)
    audit_rules: dict = Field(default_factory=dict)
    transition_rules: dict = Field(default_factory=dict)
    max_parallel_tasks: Optional[int] = None
    escalation_rules: dict = Field(default_factory=dict)
    derived_runtime_rules: dict = Field(default_factory=dict)




class RuntimeProfileContextResponse(BaseModel):
    runtime_profile_id: str
    name: str
    revision: int
    managed_sections: list[str] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)
    source: str

class RuntimeRoutingDecisionResponse(BaseModel):
    matched_agent_id: Optional[str] = None
    matched_agent_type: Optional[str] = None
    policy_profile_id: Optional[str] = None
    capability_profile_id: Optional[str] = None
    reason: str
    execution_mode: str = "sync"
    runtime_target: Optional[RuntimeTargetInfoResponse] = None
    capability_context: Optional[RuntimeCapabilityContextResponse] = None
    policy_context: Optional[RuntimePolicyContextResponse] = None


class AgentRuntimeContextResponse(BaseModel):
    agent_id: str
    agent_type: str
    capability_profile_id: Optional[str] = None
    policy_profile_id: Optional[str] = None
    capability_context: RuntimeCapabilityContextResponse
    policy_context: RuntimePolicyContextResponse
    runtime_profile_id: Optional[str] = None
    runtime_profile_context: Optional[RuntimeProfileContextResponse] = None
    runtime_target: RuntimeTargetInfoResponse
