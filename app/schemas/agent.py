from datetime import datetime

from pydantic import BaseModel
from typing import Optional


class AgentCreateRequest(BaseModel):
    name: str
    image: str
    repo_url: Optional[str] = None  # GitHub repo URL
    branch: Optional[str] = "master"  # Git branch
    disk_size_gi: int = 20
    mount_path: str = "/root/.efp"
    cpu: Optional[str] = None
    memory: Optional[str] = None
    description: Optional[str] = None


class AgentUpdateRequest(BaseModel):
    name: Optional[str] = None
    image: Optional[str] = None
    repo_url: Optional[str] = None
    branch: Optional[str] = None
    disk_size_gi: Optional[int] = None
    cpu: Optional[str] = None
    memory: Optional[str] = None
    description: Optional[str] = None


class AgentDeleteResponse(BaseModel):
    ok: bool
    destroy_data: bool


class AgentStatusResponse(BaseModel):
    id: str
    status: str
    cpu_usage: Optional[str] = None
    memory_usage: Optional[str] = None
    last_error: Optional[str] = None


class AgentResponse(BaseModel):
    id: str
    name: str
    status: str
    visibility: str
    image: str
    repo_url: Optional[str] = None
    branch: Optional[str] = None
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
