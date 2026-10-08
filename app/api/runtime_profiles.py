"""The member's connector settings as one JSON document (API clients).

The Portal UI edits these one connector at a time (``/app/connectors/...``);
this endpoint exists for scripts that want to read or replace the whole set.
Every assistant the member owns uses these settings.

``config_json`` is what the assistants get: the member's row with every
connector in ``system`` mode read from the administrator's Default
connectors. ``connector_modes`` says which mode each connector is in; a
PATCH may set them, and a section written with values that differ from the
Default connectors makes its connector ``custom`` either way.
"""
import json
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.schemas.runtime_profile import (
    redact_runtime_profile_config_for_public_response,
    RuntimeProfileResponse,
    RuntimeProfileUpdateRequest,
)
from app.services.connector_defaults_service import connector_mode, managed_specs
from app.services.runtime_profile_audit import UPDATE_RUNTIME_PROFILE, audit_runtime_profile_change
from app.services.runtime_profile_secret_service import RuntimeProfileSecretService
from app.services.runtime_profile_service import RuntimeProfileService

router = APIRouter(prefix="/api/runtime-profile", tags=["runtime-profile"])
runtime_profile_secret_service = RuntimeProfileSecretService()
logger = logging.getLogger(__name__)


def _runtime_profile_response(service: RuntimeProfileService, profile) -> RuntimeProfileResponse:
    response = RuntimeProfileResponse.model_validate(profile)
    response.config_json = json.dumps(redact_runtime_profile_config_for_public_response(service.effective_config_for(profile)))
    modes = service.connector_modes(profile)
    response.connector_modes = {spec.type: connector_mode(spec, modes) for spec in managed_specs()}
    return response


@router.get("", response_model=RuntimeProfileResponse)
def get_runtime_profile(user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = RuntimeProfileService(db)
    return _runtime_profile_response(service, service.get_or_create_for_user(user))


@router.patch("", response_model=RuntimeProfileResponse)
def update_runtime_profile(
    payload: RuntimeProfileUpdateRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = RuntimeProfileService(db)
    profile = service.get_or_create_for_user(user)
    if payload.config_json is None and not payload.connector_modes:
        return _runtime_profile_response(service, profile)

    before = service.effective_config_for(profile)
    row_changed = False
    if payload.config_json is not None:
        profile, row_changed = service.save_config(profile, payload.config_json)
    profile = service.apply_connector_modes(profile, payload.connector_modes)
    after = service.effective_config_for(profile)
    if after != before:
        if not row_changed:
            # A mode change alone moves the settings the assistants get.
            profile.revision = (profile.revision or 0) + 1
            profile = service.repo.save(profile)
        audit_runtime_profile_change(
            db,
            action=UPDATE_RUNTIME_PROFILE,
            profile_id=profile.id,
            user_id=user.id,
            before=before,
            after=after,
        )
        try:
            runtime_profile_secret_service.apply_profile_save(db, profile)
        except Exception:
            db.rollback()
            logger.exception("connector settings rollout failed profile_id=%s", profile.id)
    return _runtime_profile_response(service, profile)
