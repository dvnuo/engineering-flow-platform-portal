from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.capability_profile import CapabilityProfile
from typing import Optional


class CapabilityProfileRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **kwargs) -> CapabilityProfile:
        profile = CapabilityProfile(**kwargs)
        self.db.add(profile)
        self.db.commit()
        self.db.refresh(profile)
        return profile

    def get_by_id(self, profile_id: str) -> Optional[CapabilityProfile]:
        return self.db.get(CapabilityProfile, profile_id)

    def list_all(self) -> list[CapabilityProfile]:
        return list(self.db.scalars(select(CapabilityProfile).order_by(CapabilityProfile.created_at.desc())).all())

    def save(self, profile: CapabilityProfile) -> CapabilityProfile:
        self.db.add(profile)
        self.db.commit()
        self.db.refresh(profile)
        return profile

    def delete(self, profile: CapabilityProfile) -> None:
        self.db.delete(profile)
        self.db.commit()
