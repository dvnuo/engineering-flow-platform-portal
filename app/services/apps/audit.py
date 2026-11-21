"""``app_audit_events``: who did what to an app, and what the host reported about it.

Actions are short dotted names. A member's (or an administrator's) action
carries their id; the Portal's own housekeeping and events the host sent
(``host.<kind>``) have no actor. Details never hold a token, a visitor key or
an environment value: callers pass names and counts, and what the host sends
goes through the log redactor first.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.app import AppAuditEvent
from app.repositories.app_repo import AppAuditRepository

ACTION_CREATED = "app.created"
ACTION_UPDATED = "app.updated"
ACTION_DELETED = "app.deleted"
ACTION_VISIBILITY_CHANGED = "app.visibility_changed"
ACTION_ENV_CHANGED = "app.env_changed"
ACTION_STARTED = "app.started"
ACTION_STOPPED = "app.stopped"
ACTION_RESTARTED = "app.restarted"
ACTION_DATA_CLEARED = "app.data_cleared"
ACTION_DISABLED = "app.disabled"
ACTION_ENABLED = "app.enabled"
ACTION_ANYONE_APPROVED = "app.anyone_approved"
ACTION_ANYONE_REVOKED = "app.anyone_revoked"
ACTION_OWNER_CHANGED = "app.owner_changed"
ACTION_OWNER_INACTIVE = "app.owner_inactive"
ACTION_OWNER_ACTIVE = "app.owner_active"
ACTION_DEPLOY_REQUESTED = "deploy.requested"
ACTION_DEPLOY_SUCCEEDED = "deploy.succeeded"
ACTION_DEPLOY_FAILED = "deploy.failed"
ACTION_ROLLBACK_SUCCEEDED = "rollback.succeeded"
ACTION_ROLLBACK_FAILED = "rollback.failed"
ACTION_TOKEN_VIEWED = "token.viewed"
ACTION_TOKEN_RESET = "token.reset"
ACTION_TEMPLATE_CHANGED = "template.changed"
ACTION_TEMPLATE_VERSION_PUBLISHED = "template.version_published"
ACTION_TEMPLATE_VERSION_REMOVED = "template.version_removed"
ACTION_DATA_PURGED = "app.data_purged"
HOST_EVENT_PREFIX = "host."


def record_app_event(
    db: Session,
    app_id: str,
    action: str,
    *,
    actor_user_id: Optional[int] = None,
    detail: Optional[dict[str, Any]] = None,
    commit: bool = True,
) -> AppAuditEvent:
    """Add one audit row. ``commit=False`` leaves it to the caller's own commit."""
    return AppAuditRepository(db).create(app_id, action, actor_user_id=actor_user_id, detail=detail, commit=commit)


def record_host_event(db: Session, app_id: str, kind: str, detail: Any, at: Any = None) -> AppAuditEvent:
    """An event the host sent: stored as ``host.<kind>`` with no actor, its detail redacted."""
    from app.redaction import redact_value

    payload: dict[str, Any] = {}
    if detail:
        payload["detail"] = redact_value(detail)
    if at:
        payload["at"] = str(at)[:40]
    return record_app_event(db, app_id, f"{HOST_EVENT_PREFIX}{str(kind)[:48]}", detail=payload or None)
