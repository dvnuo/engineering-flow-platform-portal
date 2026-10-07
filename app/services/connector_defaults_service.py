"""Reset a settings connector to the administrator's Default connectors.

A member's settings row is copied from the admin seed once, when it is
created (``RuntimeProfileService.get_or_create_for_user``); the seed never
reaches a row that already exists. ``reset_to_admin_defaults`` is how a
member catches up afterwards, one connector at a time: the sections the
connector owns are replaced by the seed's, exactly as a new member would
receive them, credentials included. Pure functions over two config dicts;
the web route owns the row, the Secret and the restarts.

Only shared shapes are reset: the model provider (a personal authorization),
the commit identity and debug are never touched.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from app.schemas.runtime_profile import sanitize_runtime_profile_config_dict
from app.services.connector_registry import ConnectorSpec

# Sections a connector may own that are not a shared shape.
ADMIN_DEFAULTS_SKIPPED_SECTIONS = frozenset({"llm", "git", "debug"})


class NoAdminDefaults(Exception):
    """The seed has nothing for this connector, so there is nothing to reset to."""


def admin_defaults_sections(spec: ConnectorSpec) -> tuple[str, ...]:
    """The sections of ``spec`` a reset replaces."""

    return tuple(section for section in spec.config_sections if section not in ADMIN_DEFAULTS_SKIPPED_SECTIONS)


def admin_defaults_for(spec: ConnectorSpec, seed: Mapping[str, Any] | None) -> dict:
    """The seed's sections this connector owns, sanitized; empty when the admin set none.

    Read in the sanitized shape so nothing the save would drop is copied.
    """

    sanitized = sanitize_runtime_profile_config_dict(dict(seed or {}))
    return {
        section: copy.deepcopy(sanitized[section])
        for section in admin_defaults_sections(spec)
        if isinstance(sanitized.get(section), dict) and sanitized[section]
    }


def has_admin_defaults(spec: ConnectorSpec, seed: Mapping[str, Any] | None) -> bool:
    """Whether the panel has something to reset ``spec`` to."""

    return bool(admin_defaults_for(spec, seed))


def reset_to_admin_defaults(
    spec: ConnectorSpec, member_config: Mapping[str, Any] | None, seed: Mapping[str, Any] | None
) -> tuple[dict, list[str]]:
    """``member_config`` with this connector's sections replaced by the seed's.

    Returns ``(new_config, sections)``, the sections in sorted order. Raises
    ``NoAdminDefaults`` when the seed has nothing for the connector. A section
    the seed does not carry is left as it is, and everything outside the
    connector is untouched.
    """

    defaults = admin_defaults_for(spec, seed)
    if not defaults:
        raise NoAdminDefaults(f"Your administrator has not set Default connectors for {spec.label} yet.")
    config = copy.deepcopy(sanitize_runtime_profile_config_dict(dict(member_config or {})))
    for section, value in defaults.items():
        config[section] = value
    return config, sorted(defaults)


__all__ = [
    "ADMIN_DEFAULTS_SKIPPED_SECTIONS",
    "NoAdminDefaults",
    "admin_defaults_for",
    "admin_defaults_sections",
    "has_admin_defaults",
    "reset_to_admin_defaults",
]
