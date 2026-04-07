from datetime import datetime

from pydantic import BaseModel, Field
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


class CapabilityProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tool_set_json: Optional[str] = None
    channel_set_json: Optional[str] = None
    skill_set_json: Optional[str] = None
    allowed_external_systems_json: Optional[str] = None
    allowed_webhook_triggers_json: Optional[str] = None
    allowed_actions_json: Optional[str] = None


class CapabilityProfileResolvedData(BaseModel):
    tool_set: list[str] = Field(default_factory=list)
    channel_set: list[str] = Field(default_factory=list)
    skill_set: list[str] = Field(default_factory=list)
    allowed_external_systems: list[str] = Field(default_factory=list)
    allowed_webhook_triggers: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)


class CapabilityProfileResolvedResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    resolved: CapabilityProfileResolvedData
    created_at: datetime
    updated_at: datetime
