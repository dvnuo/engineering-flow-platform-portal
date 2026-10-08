"""Roll a change to the Default connectors out to the members who follow them.

After the administrator saves the seed, every member whose connectors in
system mode changed gets what a member's own Save gets: a bumped settings
revision, a re-rendered pod Secret, a restart of their idle assistants, and
"Restart to apply" on the busy ones. It runs after the admin's response is
sent (FastAPI background task), one member at a time, and never lets one
member's failure stop the rest. The outcome is written as one audit row.
"""
from __future__ import annotations

import logging
from datetime import datetime

from app.db import SessionLocal
from app.repositories.audit_repo import AuditRepository
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.services.connector_defaults_service import following_types, stored_connector_modes

logger = logging.getLogger(__name__)

SYNC_AUDIT_ACTION = "sync_connector_defaults"


def sync_followers(
    changed_types: list[str],
    *,
    secret_service,
    triggered_by_user_id: int | None = None,
    session_factory=None,
) -> dict:
    """Update every member who follows one of ``changed_types``; returns the counts.

    ``secret_service`` is the ``RuntimeProfileSecretService`` whose
    ``apply_profile_save`` does the Secret and the restarts; ``session_factory``
    opens the database session (the caller's ``SessionLocal``).
    """

    changed = [item for item in changed_types if item]
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
        for profile in repo.list_all():
            if not following_types(stored_connector_modes(profile.connector_modes_json), changed):
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
                target_type="platform_setting",
                target_id="runtime_profile_seed",
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


__all__ = ["SYNC_AUDIT_ACTION", "sync_followers"]
