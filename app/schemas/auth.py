from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6)
    nickname: str = Field(None, max_length=64)


class MeResponse(BaseModel):
    id: int
    username: str
    nickname: str = None
    role: str
