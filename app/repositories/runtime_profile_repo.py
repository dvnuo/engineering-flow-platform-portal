from sqlalchemy import and_, func, select
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

    def get_for_owner(self, owner_user_id: int) -> RuntimeProfile | None:
        """The member's settings row (one per member, uq_runtime_profiles_owner)."""

        return self.db.scalar(
            select(RuntimeProfile)
            .where(RuntimeProfile.owner_user_id == owner_user_id)
            .order_by(RuntimeProfile.created_at.asc(), RuntimeProfile.id.asc())
            .limit(1)
        )

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

    def count_running_bound_agents(self, profile_id: str) -> int:
        from app.models.agent import Agent

        return int(
            self.db.scalar(
                select(func.count())
                .select_from(Agent)
                .where(and_(Agent.runtime_profile_id == profile_id, func.lower(Agent.status) == "running"))
            )
            or 0
        )
