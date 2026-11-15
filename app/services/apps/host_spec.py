"""What the Portal tells the app host about an app (docs/CONTRACTS.md, sections 4.1 and 5.3).

One function builds the record for ``PUT /v1/apps/{id}`` and another the item
of the desired state the host reads when it boots, so the two can never
disagree. The environment is the owner's own variables plus the app token.
The audience sent is the one in force (``anyone`` only when approved), and an
app whose owner lost access goes out disabled.
"""

from __future__ import annotations

from typing import Any, Optional

from app.models.app import App, AppRelease
from app.models.user import User
from app.services.apps.access import app_enabled, effective_visibility
from app.services.apps.names import APP_TOKEN_ENV, DISABLED_OWNER_INACTIVE

OWNER_INACTIVE_TEXT = "The owner of this app no longer has access to the Portal."


def app_env(app: App) -> dict[str, str]:
    """The environment the host gives the app: the owner's variables, then the token (which they cannot override)."""
    env = dict(app.env)
    env[APP_TOKEN_ENV] = app.token_value
    return env


def host_disabled_reason(reason: Optional[str]) -> Optional[str]:
    """The reason as the host's page shows it to a visitor."""
    if reason == DISABLED_OWNER_INACTIVE:
        return OWNER_INACTIVE_TEXT
    return reason


def host_spec(db: Any, app: App) -> dict[str, Any]:
    """The body of ``PUT /v1/apps/{id}``."""
    owner = db.get(User, app.owner_user_id)
    enabled, reason = app_enabled(db, app)
    return {
        "slug": app.slug,
        "name": app.name,
        "owner_username": owner.username if owner is not None else "",
        "visibility": effective_visibility(app),
        "enabled": enabled,
        "disabled_reason": host_disabled_reason(reason),
        "visitor_key": app.visitor_key,
        "env": app_env(app),
        "limits": {"memory_mb": None, "data_mb": None},
    }


def current_sha(db: Any, app: App) -> Optional[str]:
    """The commit the app's current release runs; None before the first successful deployment."""
    if not app.current_release_id:
        return None
    release = db.get(AppRelease, app.current_release_id)
    return release.commit_sha if release is not None else None


def desired_app(db: Any, app: App) -> dict[str, Any]:
    """One item of ``GET /api/app-host/v1/apps``."""
    return {"id": app.id, **host_spec(db, app), "current_sha": current_sha(db, app)}
