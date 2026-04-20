from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class AutomationRuleCreate(BaseModel):
    name: str
    enabled: bool = True
    target_agent_id: str
    source_type: Literal["github"] = "github"
    trigger_type: Literal["github_pr_review_requested"] = "github_pr_review_requested"
    task_type: Literal["github_review_task"] = "github_review_task"

    owner: str
    repo: str
    review_target_type: Literal["user", "team"]
    review_target: str
    interval_seconds: int = Field(default=60, ge=30, le=3600)
    skill_name: str = "review-pull-request"
    review_event: str = "COMMENT"

    @field_validator("name", "target_agent_id", "owner", "repo", "review_target", "skill_name", "review_event")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("must not be empty")
        return cleaned

    @model_validator(mode="after")
    def _validate_review_target(self):
        if self.review_target_type == "user" and any(ch.isspace() for ch in self.review_target):
            raise ValueError("review_target must not contain whitespace for user target")
        return self


class AutomationRuleUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    target_agent_id: Optional[str] = None
    owner: Optional[str] = None
    repo: Optional[str] = None
    review_target_type: Optional[Literal["user", "team"]] = None
    review_target: Optional[str] = None
    interval_seconds: Optional[int] = Field(default=None, ge=30, le=3600)
    skill_name: Optional[str] = None
    review_event: Optional[str] = None


class AutomationRuleRead(BaseModel):
    id: str
    name: str
    enabled: bool
    source_type: str
    trigger_type: str
    target_agent_id: str
    task_type: str
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
