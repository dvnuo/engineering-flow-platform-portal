import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, require_admin
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.schemas.runtime_profile import (
    RuntimeProfileCreateRequest,
    RuntimeProfileOptionResponse,
    RuntimeProfileResponse,
    RuntimeProfileUpdateRequest,
)
from app.services.runtime_profile_sync_service import RuntimeProfileSyncService

router = APIRouter(prefix="/api/runtime-profiles", tags=["runtime-profiles"])
runtime_profile_sync_service = RuntimeProfileSyncService()
logger = logging.getLogger(__name__)


@router.post("", response_model=RuntimeProfileResponse)
def create_runtime_profile(payload: RuntimeProfileCreateRequest, user=Depends(require_admin), db: Session = Depends(get_db)):
    _ = user
    repo = RuntimeProfileRepository(db)
    try:
        profile = repo.create(**payload.model_dump())
    except IntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="RuntimeProfile name already exists") from exc
    return RuntimeProfileResponse.model_validate(profile)


@router.get("", response_model=list[RuntimeProfileResponse])
def list_runtime_profiles(user=Depends(require_admin), db: Session = Depends(get_db)):
    _ = user
    profiles = RuntimeProfileRepository(db).list_all()
    return [RuntimeProfileResponse.model_validate(p) for p in profiles]


@router.get("/options", response_model=list[RuntimeProfileOptionResponse])
def list_runtime_profile_options(user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    profiles = RuntimeProfileRepository(db).list_all()
    return [
        RuntimeProfileOptionResponse(
            id=p.id,
            name=p.name,
            description=p.description,
            revision=p.revision,
        )
        for p in profiles
    ]


@router.get("/{profile_id}", response_model=RuntimeProfileResponse)
def get_runtime_profile(profile_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    _ = user
    profile = RuntimeProfileRepository(db).get_by_id(profile_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RuntimeProfile not found")
    return RuntimeProfileResponse.model_validate(profile)


@router.patch("/{profile_id}", response_model=RuntimeProfileResponse)
async def update_runtime_profile(profile_id: str, payload: RuntimeProfileUpdateRequest, user=Depends(require_admin), db: Session = Depends(get_db)):
    _ = user
    repo = RuntimeProfileRepository(db)
    profile = repo.get_by_id(profile_id)
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
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="RuntimeProfile name already exists") from exc

    if config_changed:
        try:
            await runtime_profile_sync_service.sync_profile_to_bound_agents(db, saved)
        except Exception:
            logger.exception("runtime profile fan-out sync failed profile_id=%s", saved.id)

    return RuntimeProfileResponse.model_validate(saved)


@router.delete("/{profile_id}")
def delete_runtime_profile(profile_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    _ = user
    repo = RuntimeProfileRepository(db)
    profile = repo.get_by_id(profile_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RuntimeProfile not found")

    bound_agent_count = repo.count_bound_agents(profile_id)
    if bound_agent_count > 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="RuntimeProfile is still referenced by agents")

    repo.delete(profile)
    return {"ok": True}
