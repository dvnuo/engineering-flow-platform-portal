from datetime import datetime

from pydantic import BaseModel
from typing import Optional


class RobotCreateRequest(BaseModel):
    name: str
    image: str
    disk_size_gi: int = 20
    cpu: Optional[str] = None
    memory: Optional[str] = None
    description: Optional[str] = None


class RobotDeleteResponse(BaseModel):
    ok: bool
    destroy_data: bool


class RobotStatusResponse(BaseModel):
    id: str
    status: str
    last_error: Optional[str] = None


class RobotResponse(BaseModel):
    id: str
    name: str
    status: str
    visibility: str
    image: str
    owner_user_id: int
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
