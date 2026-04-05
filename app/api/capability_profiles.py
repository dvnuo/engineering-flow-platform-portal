from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.repositories.capability_profile_repo import CapabilityProfileRepository
from app.schemas.capability_profile import CapabilityProfileCreateRequest, CapabilityProfileResponse

router = APIRouter(prefix="/api/capability-profiles", tags=["capability-profiles"])


@router.post("", response_model=CapabilityProfileResponse)
def create_capability_profile(payload: CapabilityProfileCreateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    profile = CapabilityProfileRepository(db).create(**payload.model_dump())
    return CapabilityProfileResponse.model_validate(profile)


@router.get("", response_model=list[CapabilityProfileResponse])
def list_capability_profiles(user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    profiles = CapabilityProfileRepository(db).list_all()
    return [CapabilityProfileResponse.model_validate(p) for p in profiles]


@router.get("/{profile_id}", response_model=CapabilityProfileResponse)
def get_capability_profile(profile_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    profile = CapabilityProfileRepository(db).get_by_id(profile_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CapabilityProfile not found")
    return CapabilityProfileResponse.model_validate(profile)
