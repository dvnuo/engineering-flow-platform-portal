from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.policy_profile import PolicyProfile
from typing import Optional


class PolicyProfileRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **kwargs) -> PolicyProfile:
        profile = PolicyProfile(**kwargs)
        self.db.add(profile)
        self.db.commit()
        self.db.refresh(profile)
        return profile

    def get_by_id(self, profile_id: str) -> Optional[PolicyProfile]:
        return self.db.get(PolicyProfile, profile_id)

    def list_all(self) -> list[PolicyProfile]:
        return list(self.db.scalars(select(PolicyProfile).order_by(PolicyProfile.created_at.desc())).all())

    def save(self, profile: PolicyProfile) -> PolicyProfile:
        self.db.add(profile)
        self.db.commit()
        self.db.refresh(profile)
        return profile

    def delete(self, profile: PolicyProfile) -> None:
        self.db.delete(profile)
        self.db.commit()
