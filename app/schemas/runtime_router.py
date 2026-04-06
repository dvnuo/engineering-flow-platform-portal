from pydantic import BaseModel
from typing import Optional


class ResolveBindingRequest(BaseModel):
    system_type: str
    external_account_id: str


class RuntimeTargetInfoResponse(BaseModel):
    agent_id: str
    namespace: Optional[str] = None
    service_name: Optional[str] = None
    endpoint_path: Optional[str] = None


class RuntimeRoutingDecisionResponse(BaseModel):
    matched_agent_id: Optional[str] = None
    matched_agent_type: Optional[str] = None
    policy_profile_id: Optional[str] = None
    capability_profile_id: Optional[str] = None
    reason: str
    execution_mode: str = "sync"
    runtime_target: Optional[RuntimeTargetInfoResponse] = None
