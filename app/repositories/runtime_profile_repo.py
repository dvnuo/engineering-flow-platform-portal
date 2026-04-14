from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.runtime_profile import RuntimeProfile


class RuntimeProfileRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **kwargs) -> RuntimeProfile:
        profile = RuntimeProfile(**kwargs)
        self.db.add(profile)
        self.db.commit()
        self.db.refresh(profile)
        return profile

    def get_by_id(self, profile_id: str) -> RuntimeProfile | None:
        return self.db.get(RuntimeProfile, profile_id)

    def list_all(self) -> list[RuntimeProfile]:
        return list(self.db.scalars(select(RuntimeProfile).order_by(RuntimeProfile.created_at.desc())).all())

    def save(self, profile: RuntimeProfile) -> RuntimeProfile:
        self.db.add(profile)
        self.db.commit()
        self.db.refresh(profile)
        return profile

    def delete(self, profile: RuntimeProfile) -> None:
        self.db.delete(profile)
        self.db.commit()

    def count_bound_agents(self, profile_id: str) -> int:
        from app.models.agent import Agent

        return int(self.db.scalar(select(func.count()).select_from(Agent).where(Agent.runtime_profile_id == profile_id)) or 0)
