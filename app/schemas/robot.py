from datetime import datetime

from pydantic import BaseModel


class RobotCreateRequest(BaseModel):
    name: str
    image: str
    disk_size_gi: int = 20
    cpu: str | None = None
    memory: str | None = None
    description: str | None = None


class RobotResponse(BaseModel):
    id: str
    name: str
    status: str
    visibility: str
    image: str
    owner_user_id: int
    created_at: datetime

    class Config:
        from_attributes = True
