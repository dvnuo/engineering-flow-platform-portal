from datetime import datetime

from pydantic import BaseModel
from typing import Optional


class ExternalEventSubscriptionCreateRequest(BaseModel):
    agent_id: str
    source_type: str
    event_type: str
    target_ref: Optional[str] = None
    enabled: bool = True
    config_json: Optional[str] = None
    dedupe_key_template: Optional[str] = None
    mode: str = "push"
    source_kind: Optional[str] = None
    binding_id: Optional[str] = None
    scope_json: Optional[str] = None
    matcher_json: Optional[str] = None
    routing_json: Optional[str] = None
    poll_profile_json: Optional[str] = None


class ExternalEventSubscriptionResponse(BaseModel):
    id: str
    agent_id: str
    source_type: str
    event_type: str
    target_ref: Optional[str] = None
    enabled: bool
    config_json: Optional[str] = None
    dedupe_key_template: Optional[str] = None
    mode: Optional[str] = "push"
    source_kind: Optional[str] = None
    binding_id: Optional[str] = None
    scope_json: Optional[str] = None
    matcher_json: Optional[str] = None
    routing_json: Optional[str] = None
    poll_profile_json: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
