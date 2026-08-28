from datetime import datetime

from pydantic import BaseModel, field_validator
from typing import Optional

from app.contracts.runtime_type import normalize_runtime_type


class AssistantTypeResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    icon: str
    runtime_type: str
    agent_settings_branch: Optional[str] = None
    skill_branch: Optional[str] = None
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AssistantTypeCreateRequest(BaseModel):
    model_config = {"extra": "ignore"}
    name: str
    description: Optional[str] = None
    icon: str = "bot"
    runtime_type: str = "native"
    agent_settings_branch: Optional[str] = None
    skill_branch: Optional[str] = None
    sort_order: int = 0
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("name is required")
        return normalized

    @field_validator("icon")
    @classmethod
    def validate_icon(cls, value: str) -> str:
        return (value or "").strip() or "bot"

    @field_validator("runtime_type")
    @classmethod
    def validate_runtime_type(cls, value: str) -> str:
        return normalize_runtime_type(value or "native")

    @field_validator("agent_settings_branch", "skill_branch", "description")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class AssistantTypeUpdateRequest(BaseModel):
    model_config = {"extra": "ignore"}
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    runtime_type: Optional[str] = None
    agent_settings_branch: Optional[str] = None
    skill_branch: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None

    @field_validator("runtime_type")
    @classmethod
    def validate_runtime_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_runtime_type(value)


class SimpleAgentCreateRequest(BaseModel):
    """Everything simple mode asks for: a name and which kind of assistant."""

    model_config = {"extra": "ignore"}
    name: str
    assistant_type_id: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("name is required")
        return normalized
