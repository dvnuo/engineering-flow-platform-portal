"""Audit rows for runtime profile changes.

A profile carries credentials, so its audit row must say *what* changed
without ever saying what it changed *to*: the sections touched, and the dotted
paths of the secret fields whose value differs (``aws.password``,
``splunk.instances[0].token``). Key names only, never values.

The diff runs over the sanitized before/after configs, so a save that touches
nothing the portal manages leaves an empty list rather than noise.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.repositories.audit_repo import AuditRepository
from app.services.profile_secret_encryption import SENSITIVE_FIELD_NAMES

logger = logging.getLogger(__name__)

RUNTIME_PROFILE_TARGET_TYPE = "runtime_profile"
CREATE_RUNTIME_PROFILE = "create_runtime_profile"
UPDATE_RUNTIME_PROFILE = "update_runtime_profile"
DELETE_RUNTIME_PROFILE = "delete_runtime_profile"


def _secret_values_by_path(value: Any, path: str = "", found: dict[str, str] | None = None) -> dict[str, str]:
    """Map every non-empty secret field to its dotted path (the value is only compared)."""
    if found is None:
        found = {}
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key in SENSITIVE_FIELD_NAMES and isinstance(child, str):
                if child.strip():
                    found[child_path] = child
                continue
            _secret_values_by_path(child, child_path, found)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _secret_values_by_path(child, f"{path}[{index}]", found)
    return found


def runtime_profile_config_changes(before: dict | None, after: dict | None) -> dict[str, list[str]]:
    """Which top-level sections and which secret fields differ between two configs.

    A section counts as changed when it appears on one side only or its value
    differs; a secret field when it was added, removed or given a new value.
    Both lists are sorted so a row reads the same however the diff was built.
    """
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    sections = sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
    secrets_before = _secret_values_by_path(before)
    secrets_after = _secret_values_by_path(after)
    secret_fields_changed = sorted(
        path
        for path in set(secrets_before) | set(secrets_after)
        if secrets_before.get(path) != secrets_after.get(path)
    )
    return {"sections": sections, "secret_fields_changed": secret_fields_changed}


def audit_runtime_profile_change(
    db: Session,
    *,
    action: str,
    profile_id: str,
    user_id: int | None,
    before: dict | None,
    after: dict | None,
) -> None:
    """Write one audit row for a profile create/update/delete.

    Never raises: an audit failure must not undo a save that already happened,
    so it is logged and the request goes on.
    """
    try:
        AuditRepository(db).create(
            action=action,
            target_type=RUNTIME_PROFILE_TARGET_TYPE,
            target_id=str(profile_id),
            user_id=user_id,
            details=runtime_profile_config_changes(before, after),
        )
    except Exception:  # pragma: no cover - the save itself is what matters
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception("runtime profile audit failed profile_id=%s action=%s", profile_id, action)
