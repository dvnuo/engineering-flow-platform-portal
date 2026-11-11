"""Groups and their Default connectors (app/services/group_service.py).

A system administrator creates, renames and deletes groups and edits any
group's Default connectors; a group administrator reads their own group and
edits its Default connectors. Saving a group's Default connectors rolls the
change out to the members who follow them, like the platform's do.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.deps import require_admin, require_administrator
from app.i18n import N_
from app.models.group import Group
from app.services.authorization import administers_group, is_system_admin
from app.services.connector_defaults_service import group_choice, group_specs
from app.services.connector_defaults_sync import rollout_profiles, sync_followers
from app.services.group_service import GROUP_NAME_MAX_LENGTH, GroupService
from app.services.runtime_profile_secret_service import RuntimeProfileSecretService
from app.services.runtime_profile_seed_service import redact_seed_for_display

router = APIRouter(prefix="/api/groups", tags=["groups"])
runtime_profile_secret_service = RuntimeProfileSecretService()


class GroupCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=GROUP_NAME_MAX_LENGTH)
    description: str | None = None


class GroupUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=GROUP_NAME_MAX_LENGTH)
    description: str | None = None


def _group_response(group: Group, counts: dict[int, int] | None = None) -> dict:
    return {
        "id": group.id,
        "name": group.name,
        "description": group.description,
        "is_default": bool(group.is_default),
        "member_count": (counts or {}).get(group.id, 0),
        "created_at": group.created_at,
        "updated_at": group.updated_at,
    }


def _group_or_404(groups: GroupService, group_id: int) -> Group:
    group = groups.repo.get_by_id(group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=N_("Group not found"))
    return group


def _administered_group_or_404(actor, groups: GroupService, group_id: int) -> Group:
    """The group, when ``actor`` administers it; 404 otherwise, so other groups stay unseen."""

    group = _group_or_404(groups, group_id)
    if not administers_group(actor, group.id, groups.default_group_id()):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=N_("Group not found"))
    return group


@router.get("")
def list_groups(actor=Depends(require_administrator), db: Session = Depends(get_db)):
    groups = GroupService(db)
    groups.default_group()
    counts = groups.repo.member_counts()
    rows = groups.repo.list_all()
    if not is_system_admin(actor):
        own = groups.group_of(actor)
        rows = [group for group in rows if group.id == own.id]
    return [_group_response(group, counts) for group in rows]


@router.post("")
def create_group(payload: GroupCreateRequest, admin=Depends(require_admin), db: Session = Depends(get_db)):
    try:
        group = GroupService(db).create_group(payload.name, description=payload.description, actor_id=admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _group_response(group)


@router.patch("/{group_id}")
def update_group(group_id: int, payload: GroupUpdateRequest, admin=Depends(require_admin), db: Session = Depends(get_db)):
    groups = GroupService(db)
    group = _group_or_404(groups, group_id)
    try:
        group = groups.update_group(group, name=payload.name, description=payload.description, actor_id=admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _group_response(group, groups.repo.member_counts())


@router.delete("/{group_id}")
def delete_group(
    group_id: int,
    background_tasks: BackgroundTasks,
    move_to: int = Query(..., description="The group that takes the deleted group's members."),
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Move the group's members (and pending allowlist entries) to ``move_to``, then delete it."""

    groups = GroupService(db)
    group = _group_or_404(groups, group_id)
    target = _group_or_404(groups, move_to)
    try:
        result = groups.delete_group(group, target, actor_id=admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if result.profile_ids:
        background_tasks.add_task(
            rollout_profiles,
            result.profile_ids,
            secret_service=runtime_profile_secret_service,
            triggered_by_user_id=admin.id,
            session_factory=SessionLocal,
            reason="group_deleted",
        )
    return {"ok": True, "moved": len(result.moved), "pending_entries": result.pending_entries, "moved_to": target.id}


@router.get("/{group_id}/defaults")
def get_group_defaults(group_id: int, actor=Depends(require_administrator), db: Session = Depends(get_db)):
    """The group's own values, what it does with each connector, and what its members get (credentials masked)."""

    groups = GroupService(db)
    group = _administered_group_or_404(actor, groups, group_id)
    config = groups.config_of(group)
    modes = groups.modes_of(group)
    return {
        "group": _group_response(group, groups.repo.member_counts()),
        "choices": {spec.type: group_choice(spec, config, modes) for spec in group_specs()},
        "config": config,
        "effective": redact_seed_for_display(groups.seed_for_group(group)),
    }


@router.put("/{group_id}/defaults")
def put_group_defaults(
    group_id: int,
    background_tasks: BackgroundTasks,
    payload: dict = Body(...),
    actor=Depends(require_administrator),
    db: Session = Depends(get_db),
):
    """Store what the group sets itself and roll the change out to its members who follow it.

    Body: ``{"choices": {connector type: "platform" | "custom" | "none"},
    "config": {section: values}}``; a connector left out of ``choices`` keeps
    its current choice. The proxy is the platform's and cannot be set here.
    """

    groups = GroupService(db)
    group = _administered_group_or_404(actor, groups, group_id)
    choices = payload.get("choices") if isinstance(payload, dict) else None
    config = payload.get("config") if isinstance(payload, dict) else None
    if not isinstance(choices, dict) or (config is not None and not isinstance(config, dict)):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=N_("choices must be an object"))
    try:
        changed = groups.save_group_defaults(group, config or {}, choices, actor_id=actor.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if changed:
        background_tasks.add_task(
            sync_followers,
            changed,
            secret_service=runtime_profile_secret_service,
            triggered_by_user_id=actor.id,
            session_factory=SessionLocal,
            changed_by_group={group.id: changed},
            audit_target=("group", str(group.id)),
        )
    return {**get_group_defaults(group.id, actor, db), "changed_connectors": changed}
