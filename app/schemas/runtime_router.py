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
    reason: str
    execution_mode: str = "sync"
    runtime_target: Optional[RuntimeTargetInfoResponse] = None
    runtime_profile_id: Optional[str] = None
    runtime_profile_context: Optional[RuntimeProfileContextResponse] = None


class AgentRuntimeContextResponse(BaseModel):
    agent_id: str
    agent_type: str
    runtime_profile_id: Optional[str] = None
    runtime_profile_context: Optional[RuntimeProfileContextResponse] = None
    runtime_target: RuntimeTargetInfoResponse
