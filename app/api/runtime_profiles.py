import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.schemas.runtime_profile import (
    RuntimeProfileCreateRequest,
    RuntimeProfileOptionResponse,
    RuntimeProfileResponse,
    RuntimeProfileUpdateRequest,
)
from app.services.runtime_profile_service import RuntimeProfileService
from app.services.runtime_profile_sync_service import RuntimeProfileSyncService

router = APIRouter(prefix="/api/runtime-profiles", tags=["runtime-profiles"])
runtime_profile_sync_service = RuntimeProfileSyncService()
runtime_profile_service = RuntimeProfileService()
logger = logging.getLogger(__name__)


@router.post("", response_model=RuntimeProfileResponse)
def create_runtime_profile(payload: RuntimeProfileCreateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo = RuntimeProfileRepository(db)
    try:
        profile = runtime_profile_service.create_profile_for_user(
            db,
            user.id,
            name=payload.name,
            description=payload.description,
            config_json=payload.config_json,
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="RuntimeProfile name already exists") from exc

    return RuntimeProfileResponse.model_validate(profile)


@router.get("", response_model=list[RuntimeProfileResponse])
def list_runtime_profiles(user=Depends(get_current_user), db: Session = Depends(get_db)):
    profiles = RuntimeProfileRepository(db).list_by_owner(user.id)
    return [RuntimeProfileResponse.model_validate(p) for p in profiles]


@router.get("/options", response_model=list[RuntimeProfileOptionResponse])
def list_runtime_profile_options(user=Depends(get_current_user), db: Session = Depends(get_db)):
    profiles = RuntimeProfileRepository(db).list_by_owner_for_options(user.id)
    return [
        RuntimeProfileOptionResponse(
            id=p.id,
            name=p.name,
            description=p.description,
            revision=p.revision,
            is_default=p.is_default,
        )
        for p in profiles
    ]


@router.get("/{profile_id}", response_model=RuntimeProfileResponse)
def get_runtime_profile(profile_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    profile = RuntimeProfileRepository(db).get_by_id_for_owner(profile_id, user.id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RuntimeProfile not found")
    return RuntimeProfileResponse.model_validate(profile)


@router.patch("/{profile_id}", response_model=RuntimeProfileResponse)
async def update_runtime_profile(profile_id: str, payload: RuntimeProfileUpdateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo = RuntimeProfileRepository(db)
    profile = repo.get_by_id_for_owner(profile_id, user.id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RuntimeProfile not found")

    changes = payload.model_dump(exclude_unset=True)
    config_changed = "config_json" in changes
    for field, value in changes.items():
        setattr(profile, field, value)

    if config_changed:
        profile.revision = (profile.revision or 0) + 1

    try:
        saved = repo.save(profile)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="RuntimeProfile name already exists") from exc

    if config_changed:
        try:
            await runtime_profile_sync_service.sync_profile_to_bound_agents(db, saved)
        except Exception:
            logger.exception("runtime profile fan-out sync failed profile_id=%s", saved.id)

    return RuntimeProfileResponse.model_validate(saved)


@router.post("/{profile_id}/set-default", response_model=RuntimeProfileResponse)
def set_runtime_profile_default(profile_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        profile = runtime_profile_service.set_default_profile(db, user.id, profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RuntimeProfile not found") from exc
    return RuntimeProfileResponse.model_validate(profile)


@router.delete("/{profile_id}")
def delete_runtime_profile(profile_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo = RuntimeProfileRepository(db)
    profile = repo.get_by_id_for_owner(profile_id, user.id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RuntimeProfile not found")

    bound_agent_count = repo.count_bound_agents(profile_id)
    if bound_agent_count > 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="RuntimeProfile is still referenced by agents")

    ok, message = runtime_profile_service.delete_profile_for_user(db, user.id, profile_id)
    if not ok:
        if message == "Each user must keep at least one runtime profile.":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RuntimeProfile not found")

    return {"ok": True}
