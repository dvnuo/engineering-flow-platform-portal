from datetime import datetime

from pydantic import BaseModel


class RobotCreateRequest(BaseModel):
    name: str
    image: str
    disk_size_gi: int = 20
    cpu: str | None = None
    memory: str | None = None
    description: str | None = None


class RobotStatusResponse(BaseModel):
    id: str
    status: str
    last_error: str | None = None


class RobotResponse(BaseModel):
    id: str
    name: str
    status: str
    visibility: str
    image: str
    owner_user_id: int
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
