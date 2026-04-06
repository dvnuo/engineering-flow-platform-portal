from datetime import datetime

from pydantic import BaseModel
from typing import Optional


class CapabilityProfileCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    tool_set_json: Optional[str] = None
    channel_set_json: Optional[str] = None
    skill_set_json: Optional[str] = None
    allowed_external_systems_json: Optional[str] = None
    allowed_webhook_triggers_json: Optional[str] = None
    allowed_actions_json: Optional[str] = None


class CapabilityProfileResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    tool_set_json: Optional[str] = None
    channel_set_json: Optional[str] = None
    skill_set_json: Optional[str] = None
    allowed_external_systems_json: Optional[str] = None
    allowed_webhook_triggers_json: Optional[str] = None
    allowed_actions_json: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
