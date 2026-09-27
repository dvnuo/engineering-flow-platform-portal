"""The member's connector settings as one JSON document (API clients).

The Portal UI edits these one connector at a time (``/app/connectors/...``);
this endpoint exists for scripts that want to read or replace the whole set.
Every assistant the member owns uses these settings.
"""
import json
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.schemas.runtime_profile import (
    parse_runtime_profile_config_json,
    redact_runtime_profile_config_for_public_response,
    RuntimeProfileResponse,
    RuntimeProfileUpdateRequest,
)
from app.services.runtime_profile_audit import UPDATE_RUNTIME_PROFILE, audit_runtime_profile_change
from app.services.runtime_profile_secret_service import RuntimeProfileSecretService
from app.services.runtime_profile_service import RuntimeProfileService

router = APIRouter(prefix="/api/runtime-profile", tags=["runtime-profile"])
runtime_profile_secret_service = RuntimeProfileSecretService()
logger = logging.getLogger(__name__)


def _runtime_profile_response(profile) -> RuntimeProfileResponse:
    response = RuntimeProfileResponse.model_validate(profile)
    parsed = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
    response.config_json = json.dumps(redact_runtime_profile_config_for_public_response(parsed))
    return response


@router.get("", response_model=RuntimeProfileResponse)
def get_runtime_profile(user=Depends(get_current_user), db: Session = Depends(get_db)):
    return _runtime_profile_response(RuntimeProfileService(db).get_or_create_for_user(user))


@router.patch("", response_model=RuntimeProfileResponse)
def update_runtime_profile(
    payload: RuntimeProfileUpdateRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = RuntimeProfileService(db)
    profile = service.get_or_create_for_user(user)
    if payload.config_json is None:
        return _runtime_profile_response(profile)

    before = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
    profile, config_changed = service.save_config(profile, payload.config_json)
    if config_changed:
        audit_runtime_profile_change(
            db,
            action=UPDATE_RUNTIME_PROFILE,
            profile_id=profile.id,
            user_id=user.id,
            before=before,
            after=parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True),
        )
        try:
            runtime_profile_secret_service.apply_profile_save(db, profile)
        except Exception:
            db.rollback()
            logger.exception("connector settings rollout failed profile_id=%s", profile.id)
    return _runtime_profile_response(profile)
