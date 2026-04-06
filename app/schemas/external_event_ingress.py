from pydantic import BaseModel
from typing import Optional


class ExternalEventIngressRequest(BaseModel):
    source_type: str
    event_type: str
    external_account_id: Optional[str] = None
    target_ref: Optional[str] = None
    dedupe_key: Optional[str] = None
    payload_json: Optional[str] = None
    metadata_json: Optional[str] = None


class ExternalEventIngressResponse(BaseModel):
    accepted: bool
    matched_subscription_ids: list[str]
    routing_reason: str
    matched_agent_id: Optional[str] = None
    created_task_id: Optional[str] = None
    deduped: bool = False
    message: str
