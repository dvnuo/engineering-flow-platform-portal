from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
from app.repositories.capability_profile_repo import CapabilityProfileRepository
from app.schemas.capability_profile import (
    CapabilityProfileCreateRequest,
    CapabilityProfileResolvedResponse,
    CapabilityProfileResponse,
    CapabilityProfileUpdateRequest,
)
from app.services.capability_context_service import CapabilityContextService, CapabilityProfileValidationError

router = APIRouter(prefix="/api/capability-profiles", tags=["capability-profiles"])
capability_context_service = CapabilityContextService()


@router.post("", response_model=CapabilityProfileResponse)
def create_capability_profile(payload: CapabilityProfileCreateRequest, user=Depends(require_admin), db: Session = Depends(get_db)):
    _ = user
    payload_dict = payload.model_dump()
    try:
        capability_context_service.validate_profile_payload(payload_dict, db=db)
    except CapabilityProfileValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.detail) from exc

    profile = CapabilityProfileRepository(db).create(**payload_dict)
    return CapabilityProfileResponse.model_validate(profile)


@router.get("", response_model=list[CapabilityProfileResponse])
def list_capability_profiles(user=Depends(require_admin), db: Session = Depends(get_db)):
    _ = user
    profiles = CapabilityProfileRepository(db).list_all()
    return [CapabilityProfileResponse.model_validate(p) for p in profiles]


@router.get("/{profile_id}", response_model=CapabilityProfileResponse)
def get_capability_profile(profile_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    _ = user
    profile = CapabilityProfileRepository(db).get_by_id(profile_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CapabilityProfile not found")
    return CapabilityProfileResponse.model_validate(profile)


@router.patch("/{profile_id}", response_model=CapabilityProfileResponse)
def update_capability_profile(
    profile_id: str,
    payload: CapabilityProfileUpdateRequest,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ = user
    repo = CapabilityProfileRepository(db)
    profile = repo.get_by_id(profile_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CapabilityProfile not found")

    changes = payload.model_dump(exclude_unset=True)
    try:
        capability_context_service.validate_profile_payload(changes, db=db)
    except CapabilityProfileValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.detail) from exc

    for field, value in changes.items():
        setattr(profile, field, value)

    saved = repo.save(profile)
    return CapabilityProfileResponse.model_validate(saved)


@router.delete("/{profile_id}")
def delete_capability_profile(profile_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    repo = CapabilityProfileRepository(db)
    profile = repo.get_by_id(profile_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CapabilityProfile not found")
    repo.delete(profile)
    return {"ok": True}


@router.get("/{profile_id}/resolved", response_model=CapabilityProfileResolvedResponse)
def get_capability_profile_resolved(
    profile_id: str,
    agent_id: str | None = None,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ = user
    profile = CapabilityProfileRepository(db).get_by_id(profile_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CapabilityProfile not found")

    try:
        resolved = capability_context_service.resolve_profile(profile)
        runtime_context = capability_context_service.build_runtime_capability_context(profile.id, resolved, db=db, agent_id=agent_id)
    except CapabilityProfileValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.detail) from exc

    return CapabilityProfileResolvedResponse(
        id=profile.id,
        name=profile.name,
        description=profile.description,
        resolved=resolved.model_copy(update=runtime_context),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )
