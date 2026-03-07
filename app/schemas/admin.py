from datetime import datetime

from pydantic import BaseModel
from typing import Optional


class AuditLogResponse(BaseModel):
    id: int
    user_id: Optional[int]
    action: str
    target_type: str
    target_id: str
    details_json: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True
