from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.agent_delegation import AgentDelegationBoardItemResponse


class AgentCoordinationRunResponse(BaseModel):
    coordination_run_id: str
    group_id: str
    leader_agent_id: str
    origin_session_id: str | None = None
    status: str
    latest_round_index: int
    summary: dict | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    items: list[AgentDelegationBoardItemResponse] = Field(default_factory=list)

    @classmethod
    def from_model(cls, row):
        import json

        summary = None
        if row.summary_json:
            try:
                parsed = json.loads(row.summary_json)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                summary = parsed
        return cls(
            coordination_run_id=row.coordination_run_id,
            group_id=row.group_id,
            leader_agent_id=row.leader_agent_id,
            origin_session_id=row.origin_session_id,
            status=row.status,
            latest_round_index=row.latest_round_index,
            summary=summary,
            created_at=row.created_at,
            updated_at=row.updated_at,
            completed_at=row.completed_at,
        )
