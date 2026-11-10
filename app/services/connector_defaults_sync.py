"""Roll a change to the Default connectors out to the members who follow them.

After an administrator saves the platform's or a group's Default connectors,
every member whose connectors in system mode changed gets what a member's
own Save gets: a bumped settings revision, a re-rendered pod Secret, a
restart of their idle assistants, and "Restart to apply" on the busy ones.
It runs after the admin's response is sent (FastAPI background task), one
member at a time, and never lets one member's failure stop the rest. The
outcome is written as one audit row. ``rollout_profiles`` does the same for
rows whose revision a caller already bumped (members who changed group).
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime

from sqlalchemy import select

from app.db import SessionLocal
from app.models.user import User
from app.repositories.audit_repo import AuditRepository
from app.repositories.group_repo import GroupRepository
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.schemas.runtime_profile import parse_runtime_profile_config_json
from app.services.connector_defaults_service import follows_changed_defaults, stored_connector_modes

logger = logging.getLogger(__name__)

SYNC_AUDIT_ACTION = "sync_connector_defaults"
ROLLOUT_AUDIT_ACTION = "rollout_member_settings"


def _owner_groups(db) -> dict[int, int | None]:
    """``{user id: group id}``, an account with no group in the default group."""

    default = GroupRepository(db).get_default()
    default_id = default.id if default is not None else None
    return {
        user_id: group_id if group_id is not None else default_id
        for user_id, group_id in db.execute(select(User.id, User.group_id)).all()
    }


def sync_followers(
    changed_types: list[str],
    *,
    secret_service,
    triggered_by_user_id: int | None = None,
    session_factory=None,
    changed_by_group: Mapping[int | None, list[str]] | None = None,
    audit_target: tuple[str, str] = ("platform_setting", "runtime_profile_seed"),
) -> dict:
    """Update every member who follows one of the changed connectors; returns the counts.

    ``secret_service`` is the ``RuntimeProfileSecretService`` whose
    ``apply_profile_save`` does the Secret and the restarts; ``session_factory``
    opens the database session (the caller's ``SessionLocal``).
    ``changed_by_group`` is ``{group id: changed connectors}`` when the change
    reaches each group differently (a platform change that some groups
    override, or one group's own save): a member gets their group's list,
    and nothing when their group is not in it. Without it ``changed_types``
    reaches every member.
    """

    changed = [item for item in changed_types if item]
    if changed_by_group is not None:
        changed = sorted({item for items in changed_by_group.values() for item in (items or []) if item})
    summary = {
        "connectors": sorted(changed),
        "members": 0,
        "restarted": 0,
        "pending": 0,
        "failed_restarts": 0,
        "failed_members": 0,
        "started_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }
    if not changed:
        return summary
    db = (session_factory or SessionLocal)()
    try:
        repo = RuntimeProfileRepository(db)
        owner_groups = _owner_groups(db) if changed_by_group is not None else {}
        for profile in repo.list_all():
            reaching = changed
            if changed_by_group is not None:
                reaching = list(changed_by_group.get(owner_groups.get(profile.owner_user_id)) or [])
                if not reaching:
                    continue
            member_config = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
            if not follows_changed_defaults(member_config, stored_connector_modes(profile.connector_modes_json), reaching):
                continue
            summary["members"] += 1
            try:
                # The member's effective settings changed, so running pods are
                # on an older revision until they restart.
                profile.revision = (profile.revision or 0) + 1
                repo.save(profile)
                result = secret_service.apply_profile_save(db, profile)
            except Exception:
                db.rollback()
                summary["failed_members"] += 1
                logger.exception("Default connectors rollout failed profile_id=%s", profile.id)
                continue
            summary["restarted"] += len(result.get("restarted_agent_ids") or [])
            summary["pending"] += len(result.get("pending_agent_ids") or [])
            summary["failed_restarts"] += len(result.get("failed_agent_ids") or [])
        try:
            AuditRepository(db).create(
                action=SYNC_AUDIT_ACTION,
                target_type=audit_target[0],
                target_id=audit_target[1],
                user_id=triggered_by_user_id,
                details=summary,
            )
        except Exception:
            db.rollback()
            logger.exception("Default connectors rollout audit failed")
    finally:
        db.close()
    logger.info("Default connectors rollout: %s", summary)
    return summary


def rollout_profiles(
    profile_ids: list[str],
    *,
    secret_service,
    triggered_by_user_id: int | None = None,
    session_factory=None,
    reason: str = "",
) -> dict:
    """Secret and restarts for rows whose settings a caller already changed (and whose revision it bumped).

    Members who changed group, or whose proxy now follows the platform. One
    member at a time, one failure never stopping the rest, one audit row.
    """

    ids = [item for item in dict.fromkeys(profile_ids or []) if item]
    summary = {
        "reason": reason,
        "members": 0,
        "restarted": 0,
        "pending": 0,
        "failed_restarts": 0,
        "failed_members": 0,
        "started_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }
    if not ids:
        return summary
    db = (session_factory or SessionLocal)()
    try:
        repo = RuntimeProfileRepository(db)
        for profile_id in ids:
            profile = repo.get_by_id(profile_id)
            if profile is None:
                continue
            summary["members"] += 1
            try:
                result = secret_service.apply_profile_save(db, profile)
            except Exception:
                db.rollback()
                summary["failed_members"] += 1
                logger.exception("Member settings rollout failed profile_id=%s", profile_id)
                continue
            summary["restarted"] += len(result.get("restarted_agent_ids") or [])
            summary["pending"] += len(result.get("pending_agent_ids") or [])
            summary["failed_restarts"] += len(result.get("failed_agent_ids") or [])
        try:
            AuditRepository(db).create(
                action=ROLLOUT_AUDIT_ACTION,
                target_type="runtime_profile",
                target_id=ids[0] if len(ids) == 1 else "multiple",
                user_id=triggered_by_user_id,
                details={**summary, "profile_ids": ids[:200]},
            )
        except Exception:
            db.rollback()
            logger.exception("Member settings rollout audit failed")
    finally:
        db.close()
    logger.info("Member settings rollout: %s", summary)
    return summary


__all__ = ["ROLLOUT_AUDIT_ACTION", "SYNC_AUDIT_ACTION", "rollout_profiles", "sync_followers"]
