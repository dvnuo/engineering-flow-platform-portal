"""Per-member connector settings (docs/CONNECTORS_CONTRACT.md §7).

Every registry type is always present in a member's list so the Connectors
menu renders from one call. Local connectors fall back to "disabled with
defaults" when the member has no ``user_connectors`` row; settings connectors
read their state from the member's settings row (``runtime_profiles``) and
never return its values here -- their panels render the form server-side.
``enabled_connectors_for_user`` is the hot path the chat proxy uses on every
request and stays a single query.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.repositories.audit_repo import AuditRepository
from app.repositories.user_connector_repo import UserConnectorRepository
from app.services.connector_registry import (
    KIND_LOCAL,
    LOCAL_BROWSER_PLATFORMS,
    STATE_CONNECTED,
    STATE_NOT_SET_UP,
    STATE_OFF,
    ConnectorSpec,
    get_connector_spec,
    list_connector_specs,
    state_label,
)

logger = logging.getLogger(__name__)

# One package per platform (see LOCAL_BROWSER_PLATFORMS); CI or the operator
# drops the zips built by the tools repository under app/static/downloads/.
LOCAL_BROWSER_FALLBACK_DOWNLOAD_PATH = "/static/downloads/efp-browser-bridge-{platform}.zip"


def _parse_config_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_config(spec: ConnectorSpec, raw: str | None) -> dict[str, Any]:
    """Normalize a stored config, tolerating rows written by an older schema."""

    stored = _parse_config_json(raw)
    try:
        return spec.normalized_config(stored)
    except ValueError:
        try:
            return spec.normalized_config(spec.config_defaults)
        except ValueError:
            return dict(spec.config_defaults)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat() + "Z"


def _local_entry(spec: ConnectorSpec, row) -> dict[str, Any]:
    enabled = bool(row.enabled) if row is not None else False
    state = STATE_CONNECTED if enabled else (STATE_OFF if row is not None else STATE_NOT_SET_UP)
    return {
        "type": spec.type,
        "label": spec.label,
        "kind": spec.kind,
        "category": spec.category,
        "description": spec.description,
        "icon": spec.icon,
        "enabled": enabled,
        "state": state,
        "status_label": "Enabled" if enabled else "Not enabled",
        "config": _safe_config(spec, row.config_json if row is not None else None),
        "settings": spec.server_settings(),
        "last_verified_at": _iso(row.last_verified_at) if row is not None else None,
    }


def settings_entry(spec: ConnectorSpec, member_config: Mapping[str, Any] | None) -> dict[str, Any]:
    """List entry of a settings connector, read from the member's settings.

    Values stay out: the entry says what state the connector is in, never what
    it holds.
    """

    state = spec.state_of(member_config or {})
    return {
        "type": spec.type,
        "label": spec.label,
        "kind": spec.kind,
        "category": spec.category,
        "description": spec.description,
        "icon": spec.icon,
        "enabled": state == STATE_CONNECTED,
        "state": state,
        "status_label": state_label(spec, state),
        "config": {},
        "settings": {},
        "last_verified_at": None,
    }


def member_settings(db: Session, user) -> dict[str, Any]:
    """The member's stored settings (creating the row from the seed on first use)."""

    from app.schemas.runtime_profile import parse_runtime_profile_config_json
    from app.services.runtime_profile_service import RuntimeProfileService

    profile = RuntimeProfileService(db).get_or_create_for_user(user)
    return parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)


def local_connectors_enabled() -> bool:
    return bool(get_settings().connectors_enabled)


def list_for_user(db: Session, user) -> list[dict[str, Any]]:
    specs = list_connector_specs(include_local=local_connectors_enabled())
    rows = {row.connector_type: row for row in UserConnectorRepository(db).list_by_owner(user.id)}
    member_config = member_settings(db, user) if any(spec.is_settings for spec in specs) else {}
    return [
        settings_entry(spec, member_config) if spec.is_settings else _local_entry(spec, rows.get(spec.type))
        for spec in specs
    ]


def get_for_user(db: Session, user, connector_type: str) -> dict[str, Any]:
    """Return the member's entry for ``connector_type``; KeyError when unknown."""

    spec = get_connector_spec(connector_type)
    if spec.is_settings:
        return settings_entry(spec, member_settings(db, user))
    if not local_connectors_enabled():
        raise KeyError(connector_type)
    row = UserConnectorRepository(db).get(user.id, spec.type)
    return _local_entry(spec, row)


def _require_local(spec: ConnectorSpec) -> None:
    if spec.kind != KIND_LOCAL:
        raise ValueError(f"{spec.label} is set up in its Connectors panel, not through this API.")
    if not local_connectors_enabled():
        raise KeyError(spec.type)


def update_for_user(
    db: Session,
    user,
    connector_type: str,
    *,
    enabled: bool,
    config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate and store the member's settings; ValueError on bad config."""

    spec = get_connector_spec(connector_type)
    _require_local(spec)
    normalized = spec.normalized_config(config)
    row = UserConnectorRepository(db).upsert(
        user.id,
        spec.type,
        enabled=bool(enabled),
        config_json=json.dumps(normalized, sort_keys=True),
    )
    AuditRepository(db).create(
        action="update_connector",
        target_type="connector",
        target_id=spec.type,
        user_id=user.id,
        details={"enabled": bool(enabled), "config_keys": sorted(normalized.keys())},
    )
    return _local_entry(spec, row)


def record_verification(
    db: Session,
    user,
    connector_type: str,
    *,
    ok: bool,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    spec = get_connector_spec(connector_type)
    _require_local(spec)
    payload = {"ok": bool(ok), "at": _iso(datetime.utcnow()), "details": dict(details or {})}
    row = UserConnectorRepository(db).mark_verified(
        user.id,
        spec.type,
        ok=bool(ok),
        verification_json=json.dumps(payload, sort_keys=True, default=str)[:4000],
    )
    return {"ok": bool(ok), "last_verified_at": _iso(row.last_verified_at)}


def enabled_connectors_for_user(db: Session, user_id: int | None) -> dict[str, dict[str, Any]]:
    """``{connector_type: normalized_config}`` for the member's enabled rows."""

    if user_id is None:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in UserConnectorRepository(db).list_enabled_by_owner(int(user_id)):
        try:
            spec = get_connector_spec(row.connector_type)
        except KeyError:
            continue
        if spec.kind != KIND_LOCAL:
            continue
        result[spec.type] = _safe_config(spec, row.config_json)
    return result


def local_browser_download_url(settings=None, platform: str = "") -> str:
    """Download URL of the bridge package for ``platform`` (a LOCAL_BROWSER_PLATFORMS key).

    LOCAL_BROWSER_CLI_DOWNLOAD_URL may carry ``{platform}``, which expands to
    that key; a URL without it hands one package to every system. Empty falls
    back to the per-platform zips under app/static/downloads/.
    """

    resolved = settings or get_settings()
    configured = str(getattr(resolved, "local_browser_cli_download_url", "") or "").strip()
    template = configured or LOCAL_BROWSER_FALLBACK_DOWNLOAD_PATH
    return template.replace("{platform}", platform or LOCAL_BROWSER_PLATFORMS[0][0])


def local_browser_download_links(settings=None) -> list[dict[str, str]]:
    """One download entry per offered platform, in display order."""

    return [
        {"platform": platform, "label": label, "url": local_browser_download_url(settings, platform)}
        for platform, label in LOCAL_BROWSER_PLATFORMS
    ]


def local_browser_start_url(settings=None, portal_origin: str = "") -> str:
    """First tab of the EFP browser window (LOCAL_BROWSER_START_URL).

    An absolute http(s) URL is returned as configured; a path is resolved
    against ``portal_origin`` when one is given. Empty means the bridge opens
    the Portal origin itself.
    """

    resolved = settings or get_settings()
    configured = str(getattr(resolved, "local_browser_start_url", "") or "").strip()
    if not configured or configured.lower().startswith(("http://", "https://")):
        return configured
    origin = str(portal_origin or "").strip().rstrip("/")
    if not origin:
        return configured
    return f"{origin}/{configured.lstrip('/')}"


__all__ = [
    "enabled_connectors_for_user",
    "get_for_user",
    "list_for_user",
    "local_browser_download_links",
    "local_browser_download_url",
    "local_browser_start_url",
    "member_settings",
    "record_verification",
    "settings_entry",
    "update_for_user",
]
