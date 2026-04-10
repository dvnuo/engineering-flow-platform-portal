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


class ExternalEventSubscriptionResponse(BaseModel):
    id: str
    agent_id: str
    source_type: str
    event_type: str
    target_ref: Optional[str] = None
    enabled: bool
    config_json: Optional[str] = None
    dedupe_key_template: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
