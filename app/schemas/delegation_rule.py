import json
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, field_validator, model_validator


class DelegationRuleCreate(BaseModel):
    name: str
    enabled: bool = True
    target_agent_id: str
    skill_name: str
    source: str
    interval_seconds: int = 60

    @field_validator("name", "target_agent_id", "skill_name", "source")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("must not be empty")
        return cleaned

    @field_validator("interval_seconds")
    @classmethod
    def _positive_interval(cls, value: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("interval_seconds must be an integer") from exc
        if parsed <= 0:
            raise ValueError("interval_seconds must be greater than 0")
        return parsed


class DelegationRuleUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    target_agent_id: Optional[str] = None
    skill_name: Optional[str] = None
    interval_seconds: Optional[int] = None

    @field_validator("name", "target_agent_id", "skill_name")
    @classmethod
    def _optional_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be empty")
        return cleaned

    @field_validator("interval_seconds")
    @classmethod
    def _optional_positive_interval(cls, value: int | None) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("interval_seconds must be an integer") from exc
        if parsed <= 0:
            raise ValueError("interval_seconds must be greater than 0")
        return parsed


def _parse_json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class DelegationRuleRead(BaseModel):
    id: str
    name: str
    enabled: bool
    target_agent_id: str
    skill_name: str
    source: str
    source_type: str
    trigger_type: str
    task_type: str
    interval_seconds: int
    scope_json: str
    trigger_config_json: str
    task_config_json: str
    schedule_json: str
    state_json: str
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    locked_until: Optional[datetime] = None
    owner_user_id: Optional[int] = None
    created_by_user_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _from_rule_object(cls, value):
        if isinstance(value, dict):
            data = dict(value)
        else:
            data = {
                "id": getattr(value, "id", None),
                "name": getattr(value, "name", None),
                "enabled": getattr(value, "enabled", None),
                "source_type": getattr(value, "source_type", None),
                "trigger_type": getattr(value, "trigger_type", None),
                "target_agent_id": getattr(value, "target_agent_id", None),
                "task_type": getattr(value, "task_type", None),
                "scope_json": getattr(value, "scope_json", "{}"),
                "trigger_config_json": getattr(value, "trigger_config_json", "{}"),
                "task_config_json": getattr(value, "task_config_json", "{}"),
                "schedule_json": getattr(value, "schedule_json", "{}"),
                "state_json": getattr(value, "state_json", "{}"),
                "last_run_at": getattr(value, "last_run_at", None),
                "next_run_at": getattr(value, "next_run_at", None),
                "locked_until": getattr(value, "locked_until", None),
                "owner_user_id": getattr(value, "owner_user_id", None),
                "created_by_user_id": getattr(value, "created_by_user_id", None),
                "created_at": getattr(value, "created_at", None),
                "updated_at": getattr(value, "updated_at", None),
            }
        task_config = _parse_json_object(data.get("task_config_json"))
        schedule = _parse_json_object(data.get("schedule_json"))
        data.setdefault("source", data.get("trigger_type"))
        data.setdefault("skill_name", task_config.get("skill_name") or "")
        data.setdefault("interval_seconds", int(schedule.get("interval_seconds") or 60))
        return data

    class Config:
        from_attributes = True


class DelegationRuleRunRead(BaseModel):
    id: str
    rule_id: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    found_count: int
    created_task_count: int
    skipped_count: int
    error_message: Optional[str] = None
    metrics_json: str

    class Config:
        from_attributes = True


class DelegationRuleEventRead(BaseModel):
    id: str
    rule_id: str
    dedupe_key: str
    status: str
    source_payload_json: str
    normalized_payload_json: str
    task_id: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DelegationRuleRunOnceResponse(BaseModel):
    rule_id: str
    status: str
    found_count: int
    created_task_count: int
    skipped_count: int
    run_id: str
    created_task_ids: list[str]
