from datetime import datetime

from pydantic import BaseModel


class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


class PasswordUpdateRequest(BaseModel):
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True
