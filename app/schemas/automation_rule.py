from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class AutomationRuleCreate(BaseModel):
    name: str
    enabled: bool = True
    target_agent_id: str
    source_type: str = "github"
    trigger_type: str = "github_pr_review_requested"
    task_template_id: str
    task_type: str | None = None
    scope: dict = Field(default_factory=dict)
    trigger_config: dict = Field(default_factory=dict)
    task_input_defaults: dict = Field(default_factory=dict)
    schedule: dict = Field(default_factory=lambda: {"interval_seconds": 60})

    @field_validator("name", "target_agent_id", "task_template_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("must not be empty")
        return cleaned


class AutomationRuleUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    target_agent_id: Optional[str] = None
    task_template_id: Optional[str] = None
    scope: Optional[dict] = None
    trigger_config: Optional[dict] = None
    task_input_defaults: Optional[dict] = None
    schedule: Optional[dict] = None


class AutomationRuleRead(BaseModel):
    id: str
    name: str
    enabled: bool
    source_type: str
    trigger_type: str
    target_agent_id: str
    task_type: str
    task_template_id: str
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

    class Config:
        from_attributes = True


class AutomationRuleRunRead(BaseModel):
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


class AutomationRuleEventRead(BaseModel):
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


class AutomationRuleRunOnceResponse(BaseModel):
    rule_id: str
    status: str
    found_count: int
    created_task_count: int
    skipped_count: int
    run_id: str
    created_task_ids: list[str]
