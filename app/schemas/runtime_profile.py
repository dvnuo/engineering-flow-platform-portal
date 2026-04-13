import json
from datetime import datetime

from pydantic import BaseModel, field_validator
from typing import Optional

ALLOWED_RUNTIME_PROFILE_SECTIONS = {
    "llm",
    "proxy",
    "jira",
    "confluence",
    "github",
    "git",
    "debug",
}


def validate_runtime_profile_config_json(value: str | None) -> str:
    raw = (value or "{}").strip() or "{}"
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("config_json must be valid JSON") from exc

    if not isinstance(decoded, dict):
        raise ValueError("config_json must decode to a JSON object")

    invalid_keys = sorted([key for key in decoded.keys() if key not in ALLOWED_RUNTIME_PROFILE_SECTIONS])
    if invalid_keys:
        raise ValueError(f"config_json has unsupported top-level sections: {', '.join(invalid_keys)}")

    return json.dumps(decoded)


class RuntimeProfileCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    config_json: str = "{}"

    _validate_config = field_validator("config_json", mode="before")(validate_runtime_profile_config_json)


class RuntimeProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    config_json: Optional[str] = None

    @field_validator("config_json", mode="before")
    @classmethod
    def _validate_optional_config(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return validate_runtime_profile_config_json(value)


class RuntimeProfileResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    config_json: str
    revision: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
