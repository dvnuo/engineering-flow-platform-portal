import json
from datetime import datetime

from pydantic import BaseModel, field_validator
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

    @field_validator(
        "auto_run_rules_json",
        "permission_rules_json",
        "audit_rules_json",
        "transition_rules_json",
        "escalation_rules_json",
    )
    @classmethod
    def validate_json_object_fields(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{info.field_name} must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{info.field_name} must decode to a JSON object")
        return value


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
