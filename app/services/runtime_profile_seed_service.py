"""Admin-maintained seed for every new member's default runtime profile.

The seed carries the *shape* of a connection -- instance URLs, API versions,
project and space keys -- so a new member opens Connections to find Jira
already pointing at the right site. It may also carry credentials, and that is
the admin's call, field by field: an organization that runs shared service
accounts ("the CI bot's Jenkins token") can put those in once instead of asking
every member to paste them, while a field left blank stays blank and the member
supplies their own.

Nothing is required. A seed with no credentials behaves exactly as before, so
leaving every secret field empty keeps the platform out of the business of
holding anyone's account.

Where a seeded credential ends up: ``RuntimeProfileService`` copies the seed
into the member's first profile, from which it reaches the runtime through the
``efp-profile-*`` Secret with its sensitive values encrypted
(``profile_secret_encryption``). The member owns that copy and can overwrite it
with their own credential at any time; later edits to the seed do not reach
profiles that already exist.
"""
from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session

from app.models.platform_setting import RUNTIME_PROFILE_SEED_KEY
from app.redaction import REDACTED, redact_value
from app.repositories.platform_setting_repo import PlatformSettingRepository
from app.schemas.runtime_profile import ALLOWED_RUNTIME_PROFILE_SECTIONS
from app.services.runtime_profile_config_policy import canonicalize_portal_runtime_profile_config
from app.services.profile_secret_encryption import SENSITIVE_FIELD_NAMES


def find_secret_fields(value: Any, path: str = "") -> list[str]:
    """Return dotted paths of every sensitive field carrying a non-empty value.

    Used to tell an admin which parts of the seed are shared credentials rather
    than to police them.
    """

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


def redact_seed_for_display(seed: dict) -> dict:
    """Return the seed with every credential value masked.

    The form renders each secret into a password input the admin can reveal on
    purpose; the raw "Stored value" dump has no such gesture, so it shows which
    credentials are set without putting them on screen.

    Masks exactly the fields the profile Secret encrypts, so the two agree on
    what counts as a credential, then hands the result to the general redactor
    for anything it recognizes by shape (a token in a URL, say).
    """

    if not isinstance(seed, dict):
        return {}
    masked = copy.deepcopy(seed)
    _walk_mask(masked)
    return redact_value(masked)


def _walk_mask(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in SENSITIVE_FIELD_NAMES and isinstance(child, str) and child:
                value[key] = REDACTED
            else:
                _walk_mask(child)
    elif isinstance(value, list):
        for child in value:
            _walk_mask(child)


class RuntimeProfileSeedService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings_repo = PlatformSettingRepository(db)

    def get_seed(self) -> dict:
        """Return the stored seed, keyed down to the sections a profile knows."""

        stored = self.settings_repo.get_value(RUNTIME_PROFILE_SEED_KEY, default={})
        return _only_known_sections(stored)

    def save_seed(self, config: dict, *, updated_by_user_id: int | None = None) -> dict:
        if not isinstance(config, dict):
            raise ValueError("Seed must be a JSON object.")
        canonical = canonicalize_portal_runtime_profile_config(_only_known_sections(config))
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
                summary.append({"section": section, "configured": False, "credentials": False, "detail": "Not seeded"})
                continue
            credentials = bool(find_secret_fields(value))
            suffix = " · shared credentials set" if credentials else ""
            instances = value.get("instances")
            if isinstance(instances, list) and instances:
                names = [str(item.get("name") or item.get("url") or "?") for item in instances if isinstance(item, dict)]
                summary.append(
                    {
                        "section": section,
                        "configured": True,
                        "credentials": credentials,
                        "detail": f"{len(names)} instance(s): " + ", ".join(names) + suffix,
                    }
                )
            elif value.get("base_url"):
                summary.append(
                    {
                        "section": section,
                        "configured": True,
                        "credentials": credentials,
                        "detail": str(value.get("base_url")) + suffix,
                    }
                )
            else:
                summary.append(
                    {
                        "section": section,
                        "configured": credentials,
                        "credentials": credentials,
                        "detail": "Shared credentials only" if credentials else "Not seeded",
                    }
                )
        return summary


def _only_known_sections(config: dict) -> dict:
    """Keep only recognized profile sections so the seed cannot smuggle keys in."""

    if not isinstance(config, dict):
        return {}
    return {key: value for key, value in config.items() if key in ALLOWED_RUNTIME_PROFILE_SECTIONS}
