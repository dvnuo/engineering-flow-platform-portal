"""Which of a member's connectors follow the administrator's Default connectors.

Every settings connector of a member is in one of two modes:

- ``system``: the connector's values are the administrator's **Default
  connectors** as they are *now*. Nothing is copied into the member's row;
  the values are read from the seed wherever the row is used (the panel, a
  connection test, the pod Secret), so a change the administrator makes
  reaches every member in this mode. A new member, and a connector type
  that appears later, start here.
- ``custom``: the member's own values, kept in their row; the
  administrator's later changes do not touch them. The member switches a
  connector to custom from its panel (starting from the system default) and
  can switch it back.

The modes live in ``runtime_profiles.connector_modes_json`` as ``{connector
type: mode}``; a type that is absent is ``system``. A connector's
``managed_sections`` (registry) say which sections of the row the mode
governs: the model provider manages none, so it is always the member's own,
and GitHub manages ``github`` but not the commit identity.

Pure functions over config dicts; ``RuntimeProfileService`` owns the row and
``connector_defaults_sync`` the rollout after the administrator saves.
"""
from __future__ import annotations

import copy
import json
import logging
from collections.abc import Mapping
from typing import Any

from app.schemas.runtime_profile import sanitize_runtime_profile_config_dict
from app.services.connector_registry import SETTINGS_CONNECTORS, ConnectorSpec

logger = logging.getLogger(__name__)

MODE_SYSTEM = "system"
MODE_CUSTOM = "custom"
CONNECTOR_MODES = (MODE_SYSTEM, MODE_CUSTOM)


def managed_specs() -> tuple[ConnectorSpec, ...]:
    """The settings connectors that can follow the Default connectors."""

    return tuple(spec for spec in SETTINGS_CONNECTORS if spec.managed_sections)


def sanitized_seed(seed: Mapping[str, Any] | None) -> dict:
    """The seed in the shape the row keeps, or ``{}`` when it cannot be read.

    The admin form stores some values raw, and the sanitizer refuses a few of
    them (a bracketed host in an EKS address, say). A seed like that must not
    take every member's panel down with it, so it reads as empty here, the
    way a new member's first row already treated it.
    """

    try:
        return sanitize_runtime_profile_config_dict(dict(seed or {}))
    except ValueError:
        logger.warning("Default connectors seed could not be sanitized; treating it as empty", exc_info=True)
        return {}


def managed_slice(spec: ConnectorSpec, config: Mapping[str, Any] | None) -> dict:
    """The non-empty sections of ``config`` that ``spec``'s mode governs."""

    source = config if isinstance(config, Mapping) else {}
    return {
        section: copy.deepcopy(source[section])
        for section in spec.managed_sections
        if isinstance(source.get(section), dict) and source[section]
    }


def parse_connector_modes(raw: str | None) -> dict[str, str]:
    """``{connector type: mode}`` from the stored JSON; unknown types and modes are dropped."""

    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(decoded, dict):
        return {}
    known = {spec.type for spec in managed_specs()}
    return {
        str(key): str(value).strip().lower()
        for key, value in decoded.items()
        if str(key) in known and str(value).strip().lower() in CONNECTOR_MODES
    }


def stored_connector_modes(raw: str | None) -> dict[str, str]:
    """The modes of a row as stored, NULL included.

    NULL marks a row the Portal has not classified yet (startup does that,
    ``RuntimeProfileService.backfill_connector_modes``): until then its
    values are the member's own, so every connector reads as custom.
    """

    if raw is None:
        return {spec.type: MODE_CUSTOM for spec in managed_specs()}
    return parse_connector_modes(raw)


def dump_connector_modes(modes: Mapping[str, str] | None) -> str:
    """The stored form: only the connectors that do not follow the system default."""

    kept = {key: value for key, value in dict(modes or {}).items() if value == MODE_CUSTOM}
    return json.dumps(kept, sort_keys=True)


def connector_mode(spec: ConnectorSpec, modes: Mapping[str, str] | None) -> str:
    """``spec``'s mode: custom when it manages nothing, else as stored, else system."""

    if not spec.managed_sections:
        return MODE_CUSTOM
    mode = str((modes or {}).get(spec.type) or "").strip().lower()
    return mode if mode in CONNECTOR_MODES else MODE_SYSTEM


def effective_config(
    member_config: Mapping[str, Any] | None,
    modes: Mapping[str, str] | None,
    seed: Mapping[str, Any] | None,
) -> dict:
    """The settings a member's assistants get: their row, with every connector in
    system mode read from the current Default connectors instead.

    A system-mode connector the seed has nothing for is absent, as it would be
    for a new member.
    """

    config = {key: copy.deepcopy(value) for key, value in dict(member_config or {}).items()}
    seed_config = sanitized_seed(seed)
    for spec in managed_specs():
        if connector_mode(spec, modes) != MODE_SYSTEM:
            continue
        for section in spec.managed_sections:
            value = seed_config.get(section)
            if isinstance(value, dict) and value:
                config[section] = copy.deepcopy(value)
            else:
                config.pop(section, None)
    return config


def backfill_connector_modes(member_config: Mapping[str, Any] | None, seed: Mapping[str, Any] | None) -> dict[str, str]:
    """Modes for a row that predates modes: custom where its values differ from the seed.

    A connector whose row values equal the current Default connectors (the
    member never changed the copy they were given) follows them from now on;
    one whose values differ, or that the seed does not have, stays the
    member's own, so nothing changes under anyone on upgrade.
    """

    seed_config = sanitized_seed(seed)
    try:
        member = sanitize_runtime_profile_config_dict(dict(member_config or {}))
    except ValueError:
        member = {}
    modes: dict[str, str] = {}
    for spec in managed_specs():
        mine = managed_slice(spec, member)
        if mine and mine != managed_slice(spec, seed_config):
            modes[spec.type] = MODE_CUSTOM
    return modes


def changed_connector_types(old_seed: Mapping[str, Any] | None, new_seed: Mapping[str, Any] | None) -> list[str]:
    """The connectors whose Default connectors differ between two seeds."""

    before = sanitized_seed(old_seed)
    after = sanitized_seed(new_seed)
    return [spec.type for spec in managed_specs() if managed_slice(spec, before) != managed_slice(spec, after)]


def following_types(modes: Mapping[str, str] | None, candidate_types: list[str] | None = None) -> list[str]:
    """Of ``candidate_types`` (default: every managed connector), those in system mode."""

    specs = managed_specs()
    wanted = set(candidate_types) if candidate_types is not None else {spec.type for spec in specs}
    return [spec.type for spec in specs if spec.type in wanted and connector_mode(spec, modes) == MODE_SYSTEM]


__all__ = [
    "CONNECTOR_MODES",
    "MODE_CUSTOM",
    "MODE_SYSTEM",
    "backfill_connector_modes",
    "changed_connector_types",
    "connector_mode",
    "dump_connector_modes",
    "effective_config",
    "following_types",
    "managed_slice",
    "managed_specs",
    "parse_connector_modes",
    "sanitized_seed",
    "stored_connector_modes",
]
