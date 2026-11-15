"""Names shared with the app host and the SDK (docs/CONTRACTS.md, section 1)."""

from __future__ import annotations

import re
import secrets

from app.i18n import N_
from app.services.apps.errors import AppConflict, AppValidationError
from app.services.work_visibility import VISIBILITY_PLATFORM, VISIBILITY_PRIVATE, VISIBILITY_PUBLIC

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
SLUG_MAX_LENGTH = 40
RESERVED_SLUGS = frozenset(
    {"_efp", "api", "app", "apps", "admin", "assets", "auth", "efp", "health", "login", "logout", "portal", "static", "www"}
)

VISIBILITY_ANYONE = "anyone"
APP_VISIBILITIES = (VISIBILITY_PRIVATE, VISIBILITY_PUBLIC, VISIBILITY_PLATFORM, VISIBILITY_ANYONE)

APP_TYPES = ("fastapi", "flask", "streamlit", "gradio", "command", "static")

DESIRED_RUNNING = "running"
DESIRED_STOPPED = "stopped"

RELEASE_KIND_DEPLOY = "deploy"
RELEASE_KIND_ROLLBACK = "rollback"
RELEASE_QUEUED = "queued"
RELEASE_RUNNING = "running"
RELEASE_SUCCEEDED = "succeeded"
RELEASE_FAILED = "failed"
RELEASE_ACTIVE_STATUSES = (RELEASE_QUEUED, RELEASE_RUNNING)

# The connectors an app may call through the SDK (docs/CONTRACTS.md, section 9.3).
APP_CONNECTOR_TYPES = ("jira", "confluence", "github", "jenkins", "nexus", "splunk")

# Why an app is disabled when nobody took it down: its owner lost access.
DISABLED_OWNER_INACTIVE = "owner_inactive"

# Owner-defined environment variables (docs/CONTRACTS.md, section 8).
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_RESERVED_NAMES = frozenset({"PORT", "HOME", "TMPDIR", "PATH"})
ENV_RESERVED_PREFIX = "EFP_"
ENV_MAX_VARIABLES = 100
ENV_MAX_VALUE_LENGTH = 8192
APP_TOKEN_ENV = "EFP_APP_TOKEN"


def is_valid_slug(slug: str) -> bool:
    return bool(SLUG_RE.match(slug or "")) and slug not in RESERVED_SLUGS


def check_slug(slug: str) -> str:
    """``slug`` when it follows the rule and is not reserved; an AppValidationError otherwise."""
    value = str(slug or "").strip()
    if not SLUG_RE.match(value):
        raise AppValidationError(
            N_("The address may hold 3 to 40 lowercase letters, digits and hyphens, and must start and end with a letter or a digit."),
            code="slug_invalid",
        )
    if value in RESERVED_SLUGS:
        raise AppValidationError(N_("The address {slug} is reserved. Choose another one."), code="slug_reserved", slug=value)
    return value


def slugify(name: str) -> str:
    """A slug made of ``name``: lowercase ASCII letters and digits, the rest as single hyphens.

    May come back empty or too short (a name with no ASCII letters); the
    caller then falls back to a generated one.
    """
    lowered = str(name or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug[:SLUG_MAX_LENGTH].strip("-")


def generated_slug() -> str:
    return f"app-{secrets.token_hex(4)}"


def slug_taken_error(slug: str) -> AppConflict:
    return AppConflict(N_("The address {slug} is already taken. Choose another one."), code="slug_taken", slug=slug)
