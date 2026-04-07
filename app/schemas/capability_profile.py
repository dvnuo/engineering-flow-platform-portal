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
    allowed_capability_ids: list[str] = Field(default_factory=list)
    allowed_capability_types: list[str] = Field(default_factory=list)
    allowed_adapter_actions: list[str] = Field(default_factory=list)
    unresolved_tools: list[str] = Field(default_factory=list)
    unresolved_skills: list[str] = Field(default_factory=list)
    unresolved_channels: list[str] = Field(default_factory=list)
    unresolved_actions: list[str] = Field(default_factory=list)
    resolved_action_mappings: dict[str, str] = Field(default_factory=dict)
    runtime_capability_catalog_version: Optional[str] = None
    runtime_capability_catalog_source: Optional[str] = None
    catalog_validation_mode: Optional[str] = None


class CapabilityProfileResolvedResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    resolved: CapabilityProfileResolvedData
    created_at: datetime
    updated_at: datetime
