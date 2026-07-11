import logging
import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.schemas.runtime_profile import (
    parse_runtime_profile_config_json,
    redact_runtime_profile_config_for_public_response,
    RuntimeProfileCreateRequest,
    RuntimeProfileOptionResponse,
    RuntimeProfileResponse,
    RuntimeProfileUpdateRequest,
)
from app.services.runtime_profile_secret_service import RuntimeProfileSecretService
from app.services.runtime_profile_service import RuntimeProfileService

router = APIRouter(prefix="/api/runtime-profiles", tags=["runtime-profiles"])
runtime_profile_secret_service = RuntimeProfileSecretService()
logger = logging.getLogger(__name__)


def _runtime_profile_response(service: RuntimeProfileService, profile) -> RuntimeProfileResponse:
    response = RuntimeProfileResponse.model_validate(profile)
    parsed = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
    response.config_json = json.dumps(redact_runtime_profile_config_for_public_response(parsed))
    return response


@router.post("", response_model=RuntimeProfileResponse)
def create_runtime_profile(payload: RuntimeProfileCreateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = RuntimeProfileService(db)
    profile = service.create_for_user(
        user,
        name=payload.name,
        description=payload.description,
        config_json=payload.config_json,
        is_default=payload.is_default,
    )
    try:
        runtime_profile_secret_service.sync_profile_secret(profile)
    except Exception:
        logger.exception("runtime profile secret sync failed after create profile_id=%s", profile.id)
    return _runtime_profile_response(service, profile)


@router.get("", response_model=list[RuntimeProfileResponse])
def list_runtime_profiles(user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = RuntimeProfileService(db)
    profiles = service.list_for_user(user)
    return [_runtime_profile_response(service, p) for p in profiles]


@router.get("/options", response_model=list[RuntimeProfileOptionResponse])
def list_runtime_profile_options(user=Depends(get_current_user), db: Session = Depends(get_db)):
    profiles = RuntimeProfileService(db).list_for_user(user)
    return [
        RuntimeProfileOptionResponse(
            id=p.id,
            name=p.name,
            description=p.description,
            revision=p.revision,
            is_default=bool(p.is_default),
        )
        for p in profiles
    ]


@router.get("/{profile_id}", response_model=RuntimeProfileResponse)
def get_runtime_profile(profile_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = RuntimeProfileService(db)
    profile = service.validate_profile_belongs_to_user(user, profile_id)
    return _runtime_profile_response(service, profile)


@router.patch("/{profile_id}", response_model=RuntimeProfileResponse)
async def update_runtime_profile(profile_id: str, payload: RuntimeProfileUpdateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = RuntimeProfileService(db)
    profile, config_changed = service.update_for_user(
        user,
        profile_id,
        **payload.model_dump(exclude_unset=True),
    )

    if config_changed:
        try:
            # Secret update + restart of bound running agents is the only
            # activation path; stopped agents pick the change up on next start.
            runtime_profile_secret_service.apply_profile_save(db, profile)
        except Exception:
            db.rollback()
            logger.exception("runtime profile secret save/restart failed profile_id=%s", profile.id)

    return _runtime_profile_response(service, profile)


@router.delete("/{profile_id}")
def delete_runtime_profile(profile_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    RuntimeProfileService(db).delete_for_user(user, profile_id)
    try:
        runtime_profile_secret_service.delete_profile_secret(profile_id)
    except Exception:
        logger.exception("runtime profile secret delete failed profile_id=%s", profile_id)
    return {"ok": True}
