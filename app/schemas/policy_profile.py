from datetime import datetime

from pydantic import BaseModel
from typing import Optional


class PolicyProfileCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    auto_run_rules_json: Optional[str] = None
    permission_rules_json: Optional[str] = None
    audit_rules_json: Optional[str] = None
    transition_rules_json: Optional[str] = None
    max_parallel_tasks: Optional[int] = None
    escalation_rules_json: Optional[str] = None


class PolicyProfileResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    auto_run_rules_json: Optional[str] = None
    permission_rules_json: Optional[str] = None
    audit_rules_json: Optional[str] = None
    transition_rules_json: Optional[str] = None
    max_parallel_tasks: Optional[int] = None
    escalation_rules_json: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
