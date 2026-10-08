"""Cross-field checks a connector config has to pass before it is stored.

The sanitizers (``app.schemas.runtime_profile``) keep a row that is
well-formed on its own; these checks are about rows referring to each other:
a default instance has to name one of the instances, the AWS default account
and every EKS row have to name one of the account rows. The member's Save
runs them on the posted form; the administrator's Default connectors run
them on save too, since a member who follows them would otherwise receive
settings their own Save would refuse.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.schemas.runtime_profile import (
    TROUBLESHOOTING_INSTANCE_SECTIONS,
    normalize_aws_account_id,
    sanitize_runtime_profile_config_dict,
)

TROUBLESHOOTING_LABELS = {"nexus": "Nexus", "splunk": "Splunk", "pgsql": "PostgreSQL"}


def _rows(section: Mapping[str, Any] | None, key: str) -> list[dict]:
    value = section.get(key) if isinstance(section, Mapping) else None
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def troubleshooting_default_instance_error(section: str, section_cfg: Mapping[str, Any] | None) -> str | None:
    """The default instance has to be one of the configured rows, by name."""

    section_cfg = section_cfg if isinstance(section_cfg, Mapping) else {}
    default_instance = str(section_cfg.get("default_instance") or "").strip()
    if not default_instance:
        return None
    wanted = default_instance.lower()
    for item in _rows(section_cfg, "instances"):
        if str(item.get("name") or "").strip().lower() == wanted:
            return None
    label = TROUBLESHOOTING_LABELS.get(section, section)
    return f"Default {label} instance {default_instance} must be the name of one of the instances listed below."


def aws_default_account_error(aws_cfg: Mapping[str, Any] | None) -> str | None:
    """The default account has to be one of the configured rows, by name or id."""

    aws_cfg = aws_cfg if isinstance(aws_cfg, Mapping) else {}
    default_account = str(aws_cfg.get("default_account") or "").strip()
    if not default_account:
        return None
    wanted = default_account.lower()
    for account in _rows(aws_cfg, "accounts"):
        if str(account.get("name") or "").strip().lower() == wanted:
            return None
        if str(account.get("account_id") or "").strip() == default_account:
            return None
    return f"Default AWS account {default_account} must be the name or 12-digit id of one of the accounts listed below."


def aws_eks_account_error(aws_cfg: Mapping[str, Any] | None) -> str | None:
    """Every EKS private-endpoint row has to name one of the account rows, by name or 12-digit id.

    The same rule the member's form applies to its EKS cards
    (``_settings_parse_aws_eks_clusters``), for a config that did not come
    through the form.
    """

    aws_cfg = aws_cfg if isinstance(aws_cfg, Mapping) else {}
    known: set[str] = set()
    for account in _rows(aws_cfg, "accounts"):
        known.add(str(account.get("name") or "").strip().lower())
        known.add(normalize_aws_account_id(account.get("account_id")))
    known.discard("")
    for index, row in enumerate(_rows(aws_cfg, "eks_clusters"), start=1):
        account = str(row.get("account") or "").strip()
        if not account:
            return f"EKS cluster {index} needs the account it belongs to: the name or 12-digit id of one of the AWS accounts above."
        if (normalize_aws_account_id(account) or account.lower()) not in known:
            return f"EKS cluster {index}: {account} is not one of the AWS accounts above, by name or 12-digit id."
    return None


def config_reference_error(config: Mapping[str, Any] | None, sections: tuple[str, ...] | None = None) -> str | None:
    """The first reference in ``config`` that points at nothing, or None.

    ``sections`` limits the check to those sections (a connector's own); by
    default every section is checked.
    """

    config = config if isinstance(config, Mapping) else {}
    for section in sections if sections is not None else tuple(config.keys()):
        block = config.get(section)
        if section in TROUBLESHOOTING_INSTANCE_SECTIONS:
            error = troubleshooting_default_instance_error(section, block)
            if error:
                return error
        if section == "aws":
            error = aws_default_account_error(block) or aws_eks_account_error(block)
            if error:
                return error
    return None


def seed_config_error(seed: Mapping[str, Any] | None) -> str | None:
    """Why the administrator's Default connectors cannot be stored as given, or None.

    A value the sanitizer refuses (the admin form stores some raw) and a
    reference that points at nothing are both refused here, because members
    in system mode would receive them as they are.
    """

    try:
        sanitized = sanitize_runtime_profile_config_dict(dict(seed or {}))
    except ValueError as exc:
        return f"Default connectors contain a value that cannot be stored: {exc}"
    return config_reference_error(sanitized)


__all__ = [
    "aws_default_account_error",
    "aws_eks_account_error",
    "config_reference_error",
    "seed_config_error",
    "troubleshooting_default_instance_error",
]
