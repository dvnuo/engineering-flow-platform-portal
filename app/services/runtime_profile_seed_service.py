"""Admin-maintained seed for every new member's default runtime profile.

Company policy forbids the platform from configuring a member's credentials, so
the seed deliberately carries only the *shape* of a connection — instance URLs,
API versions, project and space keys — and never a value. A new member opens
Connections to find Jira already pointing at the right site and only has to
supply their own account and token.

That boundary is enforced here rather than left to admin discipline:
``strip_secret_fields`` rejects a save that carries any field name the profile
Secret treats as sensitive, reusing the same list the encryption layer uses so
the two can never drift apart.
"""
from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session

from app.models.platform_setting import RUNTIME_PROFILE_SEED_KEY
from app.repositories.platform_setting_repo import PlatformSettingRepository
from app.schemas.runtime_profile import ALLOWED_RUNTIME_PROFILE_SECTIONS
from app.services.runtime_profile_config_policy import canonicalize_portal_runtime_profile_config
from app.services.profile_secret_encryption import SENSITIVE_FIELD_NAMES


class SeedContainsSecretError(ValueError):
    """Raised when an admin tries to put a credential into the shared seed."""

    def __init__(self, paths: list[str]) -> None:
        self.paths = paths
        joined = ", ".join(paths)
        super().__init__(
            f"The shared seed cannot contain credentials. Remove these fields: {joined}. "
            "Members supply their own credentials in Connections."
        )


def find_secret_fields(value: Any, path: str = "") -> list[str]:
    """Return dotted paths of every sensitive field carrying a non-empty value."""

    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key in SENSITIVE_FIELD_NAMES and isinstance(child, str) and child.strip():
                found.append(child_path)
                continue
            found.extend(find_secret_fields(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(find_secret_fields(child, f"{path}[{index}]"))
    return found


def strip_secret_fields(config: dict) -> dict:
    """Drop every sensitive key so a seed can never carry a credential."""

    result = copy.deepcopy(config)
    _walk_strip(result)
    return result


def _walk_strip(value: Any) -> None:
    if isinstance(value, dict):
        for key in [k for k in value if k in SENSITIVE_FIELD_NAMES]:
            value.pop(key, None)
        for child in value.values():
            _walk_strip(child)
    elif isinstance(value, list):
        for child in value:
            _walk_strip(child)


class RuntimeProfileSeedService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings_repo = PlatformSettingRepository(db)

    def get_seed(self) -> dict:
        """Return the stored seed, defensively stripped of anything sensitive.

        Stripping on read as well as write means a seed written by an older
        build (or edited directly in the database) still cannot leak a value
        into a member's profile.
        """
        stored = self.settings_repo.get_value(RUNTIME_PROFILE_SEED_KEY, default={})
        return strip_secret_fields(_only_known_sections(stored))

    def save_seed(self, config: dict, *, updated_by_user_id: int | None = None) -> dict:
        if not isinstance(config, dict):
            raise ValueError("Seed must be a JSON object.")
        offending = find_secret_fields(config)
        if offending:
            raise SeedContainsSecretError(offending)
        cleaned = strip_secret_fields(_only_known_sections(config))
        canonical = canonicalize_portal_runtime_profile_config(cleaned)
        self.settings_repo.set_value(
            RUNTIME_PROFILE_SEED_KEY,
            canonical,
            updated_by_user_id=updated_by_user_id,
        )
        return canonical

    def seed_summary(self) -> list[dict]:
        """Per-section summary for the admin panel."""

        seed = self.get_seed()
        summary = []
        for section in ("jira", "confluence", "github", "jenkins"):
            value = seed.get(section)
            if not isinstance(value, dict):
                summary.append({"section": section, "configured": False, "detail": "Not seeded"})
                continue
            instances = value.get("instances")
            if isinstance(instances, list) and instances:
                names = [str(item.get("name") or item.get("url") or "?") for item in instances if isinstance(item, dict)]
                summary.append(
                    {"section": section, "configured": True, "detail": f"{len(names)} instance(s): " + ", ".join(names)}
                )
            elif value.get("base_url"):
                summary.append({"section": section, "configured": True, "detail": str(value.get("base_url"))})
            else:
                summary.append({"section": section, "configured": False, "detail": "Not seeded"})
        return summary


def _only_known_sections(config: dict) -> dict:
    """Keep only recognized profile sections so the seed cannot smuggle keys in."""

    if not isinstance(config, dict):
        return {}
    return {key: value for key, value in config.items() if key in ALLOWED_RUNTIME_PROFILE_SECTIONS}
