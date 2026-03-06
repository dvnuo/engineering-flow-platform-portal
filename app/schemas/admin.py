from datetime import datetime

from pydantic import BaseModel


class AuditLogResponse(BaseModel):
    id: int
    user_id: int | None
    action: str
    target_type: str
    target_id: str
    details_json: str | None
    created_at: datetime

    class Config:
        from_attributes = True
