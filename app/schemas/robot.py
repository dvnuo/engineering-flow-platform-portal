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


class RobotUpdateRequest(BaseModel):
    name: Optional[str] = None
    image: Optional[str] = None
    disk_size_gi: Optional[int] = None
    cpu: Optional[str] = None
    memory: Optional[str] = None
    description: Optional[str] = None


class RobotDeleteResponse(BaseModel):
    ok: bool
    destroy_data: bool


class RobotStatusResponse(BaseModel):
    id: str
    status: str
    cpu_usage: Optional[str] = None
    memory_usage: Optional[str] = None
    last_error: Optional[str] = None


class RobotResponse(BaseModel):
    id: str
    name: str
    status: str
    visibility: str
    image: str
    owner_user_id: int
    cpu: Optional[str] = None
    memory: Optional[str] = None
    disk_size_gi: int
    description: Optional[str] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
