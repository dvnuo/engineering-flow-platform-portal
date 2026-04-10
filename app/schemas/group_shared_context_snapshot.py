from datetime import datetime

from pydantic import BaseModel


class GroupSharedContextSnapshotResponse(BaseModel):
    id: str
    group_id: str
    context_ref: str
    scope_kind: str
    created_by_user_id: int | None = None
    source_delegation_id: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class GroupSharedContextSnapshotDetailResponse(GroupSharedContextSnapshotResponse):
    payload_json: str
