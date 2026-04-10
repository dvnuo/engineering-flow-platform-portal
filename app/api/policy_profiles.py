from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
from app.repositories.policy_profile_repo import PolicyProfileRepository
from app.schemas.policy_profile import PolicyProfileCreateRequest, PolicyProfileResponse

router = APIRouter(prefix="/api/policy-profiles", tags=["policy-profiles"])


@router.post("", response_model=PolicyProfileResponse)
def create_policy_profile(payload: PolicyProfileCreateRequest, user=Depends(require_admin), db: Session = Depends(get_db)):
    _ = user
    profile = PolicyProfileRepository(db).create(**payload.model_dump())
    return PolicyProfileResponse.model_validate(profile)


@router.get("", response_model=list[PolicyProfileResponse])
def list_policy_profiles(user=Depends(require_admin), db: Session = Depends(get_db)):
    _ = user
    profiles = PolicyProfileRepository(db).list_all()
    return [PolicyProfileResponse.model_validate(p) for p in profiles]


@router.get("/{profile_id}", response_model=PolicyProfileResponse)
def get_policy_profile(profile_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    _ = user
    profile = PolicyProfileRepository(db).get_by_id(profile_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PolicyProfile not found")
    return PolicyProfileResponse.model_validate(profile)
