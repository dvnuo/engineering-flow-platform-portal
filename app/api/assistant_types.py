import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.contracts.runtime_type import (
    InvalidRuntimeType,
    normalize_enabled_runtime_types,
    normalize_runtime_type_or_default,
    require_enabled_runtime_type,
)
from app.db import get_db
from app.deps import get_current_user, require_admin
from app.models.assistant_type import AssistantType
from app.repositories.assistant_type_repo import AssistantTypeRepository
from app.repositories.audit_repo import AuditRepository
from app.schemas.assistant_type import (
    AssistantTypeCreateRequest,
    AssistantTypeResponse,
    AssistantTypeUpdateRequest,
)

router = APIRouter(prefix="/api/assistant-types", tags=["assistant-types"])
logger = logging.getLogger(__name__)
settings = get_settings()


def _require_runtime_type_enabled_or_422(runtime_type: str | None) -> str:
    """A type is a template for new assistants, so it may only name an engine
    this Portal offers for them (ENABLED_RUNTIME_TYPES)."""
    try:
        return require_enabled_runtime_type(
            runtime_type, normalize_enabled_runtime_types(settings.enabled_runtime_types)
        )
    except InvalidRuntimeType as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("", response_model=list[AssistantTypeResponse])
def list_assistant_types(
    include_inactive: bool = False,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List assistant types. Everyone reads; only admins see inactive ones."""

    repo = AssistantTypeRepository(db)
    if include_inactive and user.role == "admin":
        return repo.list_all()
    return repo.list_active()


@router.post("", response_model=AssistantTypeResponse)
def create_assistant_type(
    payload: AssistantTypeCreateRequest,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    _require_runtime_type_enabled_or_422(payload.runtime_type)
    repo = AssistantTypeRepository(db)
    assistant_type = repo.create(
        name=payload.name,
        description=payload.description,
        icon=payload.icon,
        runtime_type=payload.runtime_type,
        agent_settings_branch=payload.agent_settings_branch,
        skill_branch=payload.skill_branch,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
        created_by_user_id=admin.id,
    )
    AuditRepository(db).create(
        action="create_assistant_type",
        target_type="assistant_type",
        target_id=assistant_type.id,
        user_id=admin.id,
        details={"name": assistant_type.name, "runtime_type": assistant_type.runtime_type},
    )
    return assistant_type


@router.patch("/{type_id}", response_model=AssistantTypeResponse)
def update_assistant_type(
    type_id: str,
    payload: AssistantTypeUpdateRequest,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    repo = AssistantTypeRepository(db)
    assistant_type = _require_type(repo, type_id)

    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if field == "name":
            normalized = (value or "").strip()
            if not normalized:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="name cannot be empty",
                )
            value = normalized
        if field in {"agent_settings_branch", "skill_branch", "description"} and isinstance(value, str):
            value = value.strip() or None
        if field == "runtime_type" and value is not None:
            # The edit form sends every field back, so re-stating the current
            # engine must pass even when it is no longer offered; only a
            # switch onto another engine is subject to ENABLED_RUNTIME_TYPES.
            if value != normalize_runtime_type_or_default(assistant_type.runtime_type):
                value = _require_runtime_type_enabled_or_422(value)
        setattr(assistant_type, field, value)

    repo.save(assistant_type)
    AuditRepository(db).create(
        action="update_assistant_type",
        target_type="assistant_type",
        target_id=assistant_type.id,
        user_id=admin.id,
        details={"changes": sorted(changes.keys())},
    )
    return assistant_type


@router.delete("/{type_id}")
def delete_assistant_type(
    type_id: str,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete a type.

    Assistants created from it are unaffected: creation copies the branch values
    onto the assistant, so a type is a template rather than a live dependency.
    """
    repo = AssistantTypeRepository(db)
    assistant_type = _require_type(repo, type_id)
    name = assistant_type.name
    repo.delete(assistant_type)
    AuditRepository(db).create(
        action="delete_assistant_type",
        target_type="assistant_type",
        target_id=type_id,
        user_id=admin.id,
        details={"name": name},
    )
    return {"status": "deleted", "id": type_id}


def _require_type(repo: AssistantTypeRepository, type_id: str) -> AssistantType:
    assistant_type = repo.get_by_id(type_id)
    if not assistant_type:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant type not found")
    return assistant_type
