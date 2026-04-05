from datetime import datetime

from pydantic import BaseModel
from typing import Optional


class AgentIdentityBindingCreateRequest(BaseModel):
    system_type: str
    external_account_id: str
    username: Optional[str] = None
    scope_json: Optional[str] = None
    enabled: bool = True


class AgentIdentityBindingResponse(BaseModel):
    id: str
    agent_id: str
    system_type: str
    external_account_id: str
    username: Optional[str] = None
    scope_json: Optional[str] = None
    enabled: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
