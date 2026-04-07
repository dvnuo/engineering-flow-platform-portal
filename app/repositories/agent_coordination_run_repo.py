from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent_coordination_run import AgentCoordinationRun


class AgentCoordinationRunRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **kwargs) -> AgentCoordinationRun:
        row = AgentCoordinationRun(**kwargs)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def save(self, row: AgentCoordinationRun) -> AgentCoordinationRun:
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def get_by_coordination_run_id(self, coordination_run_id: str) -> AgentCoordinationRun | None:
        stmt = select(AgentCoordinationRun).where(AgentCoordinationRun.coordination_run_id == coordination_run_id)
        return self.db.scalars(stmt).first()

    def list_by_group_id(self, group_id: str) -> list[AgentCoordinationRun]:
        stmt = (
            select(AgentCoordinationRun)
            .where(AgentCoordinationRun.group_id == group_id)
            .order_by(AgentCoordinationRun.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def list_by_group_and_run_ids(self, group_id: str, run_ids: list[str]) -> list[AgentCoordinationRun]:
        if not run_ids:
            return []
        stmt = (
            select(AgentCoordinationRun)
            .where(AgentCoordinationRun.group_id == group_id, AgentCoordinationRun.coordination_run_id.in_(run_ids))
            .order_by(AgentCoordinationRun.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())
