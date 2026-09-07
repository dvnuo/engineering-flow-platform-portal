"""Sign-in through an external identity: company SSO or GitHub Copilot.

Portal no longer has self-service registration. A member's account is
created the first time they arrive through one of these providers, and the
allowlist still decides whether that account may actually use the portal
(``get_current_user`` re-checks it on every request, so an account that is
provisioned before the admin allowlists it simply lands on /unauthorized).
"""

from __future__ import annotations

import json
import logging
import secrets

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.user import User
from app.repositories.audit_repo import AuditRepository
from app.repositories.user_allowlist_repo import UserAllowlistRepository, normalize_username
from app.repositories.user_repo import UserRepository
from app.schemas.runtime_profile import parse_runtime_profile_config_json
from app.services.access_control_service import ALLOWED_USER_ROLES
from app.services.auth_service import hash_password
from app.services.outbound_http import github_client_kwargs
from app.services.runtime_profile_service import RuntimeProfileService

logger = logging.getLogger(__name__)
settings = get_settings()

# Copilot OAuth always runs against public github.com (see
# CopilotAuthService), so the matching user lookup is fixed too.
GITHUB_USER_API_URL = "https://api.github.com/user"


def provision_external_user(db: Session, *, username: str, display_name: str = "", source: str) -> tuple[User, bool]:
    """Find or create the portal account behind an external identity.

    Returns ``(user, created)``. A freshly created account takes the role the
    admin pre-assigned on the allowlist (the way registration used to) and
    gets the seeded default runtime profile, so the member can start from
    the admin's Default Connections right away.
    """
    normalized = normalize_username(username)
    if not normalized:
        raise ValueError("external identity did not provide a username")

    users = UserRepository(db)
    user = users.get_by_username_case_insensitive(normalized)
    created = False
    if not user:
        entry = UserAllowlistRepository(db).get_by_username(normalized)
        role = entry.role if entry and entry.role in ALLOWED_USER_ROLES else "user"
        try:
            # Nobody signs in with a password on this account; the hash only
            # satisfies the NOT NULL column and is unguessable.
            user = users.create(normalized, hash_password(secrets.token_urlsafe(32)), role, (display_name or "").strip() or None)
            created = True
        except IntegrityError:
            db.rollback()
            user = users.get_by_username_case_insensitive(normalized)
            if not user:
                raise
        if created:
            AuditRepository(db).create(
                action="register",
                target_type="user",
                target_id=str(user.id),
                user_id=user.id,
                details={"username": user.username, "via": source},
            )
            logger.info("Provisioned portal user via %s user_id=%s username=%s", source, user.id, user.username)

    users.mark_login(user)
    RuntimeProfileService(db).ensure_user_has_default_profile(user)
    return user, created


COPILOT_PROVIDER = "github_copilot"


def sync_copilot_token_to_default_profile(db: Session, user: User, token: str) -> tuple[object, bool]:
    """Put the Copilot OAuth token on the member's default runtime profile.

    Runs on every Copilot sign-in, not only the first: GitHub tokens get
    revoked or rotated, and signing in again is how a member repairs a
    profile whose Copilot credential stopped working. The default profile is
    the admin-seeded one (Default Connections), so a new member inherits the
    shared Jira/Confluence/GitHub setup and only the LLM credential is theirs.

    A default profile the member has pointed at another provider (AI
    Platform) is left alone -- signing in with Copilot must not silently
    switch the models their assistants run on. Returns ``(profile, changed)``.
    """
    service = RuntimeProfileService(db)
    profile = service.ensure_user_has_default_profile(user)
    token = (token or "").strip()
    if not token:
        return profile, False
    config = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
    config = dict(config) if isinstance(config, dict) else {}
    llm = dict(config.get("llm") or {})
    provider = str(llm.get("provider") or "").strip().lower()
    if provider and provider != COPILOT_PROVIDER:
        logger.info(
            "Copilot sign-in left default profile alone: provider=%s user_id=%s profile_id=%s",
            provider, user.id, profile.id,
        )
        return profile, False
    if provider == COPILOT_PROVIDER and str(llm.get("api_key") or "").strip() == token:
        return profile, False
    llm["provider"] = COPILOT_PROVIDER
    llm["api_key"] = token
    config["llm"] = llm
    profile.config_json = service.normalize_persisted_config_json(json.dumps(config))
    service.repo.save(profile)
    return profile, True


def portal_username_from_github_login(login: str) -> str:
    """Strip the enterprise suffix from a GitHub login.

    Enterprise-managed users log in to GitHub as "<employee id>_<enterprise>";
    the portal account is keyed by the employee id alone (GITHUB_USERNAME_SUFFIX
    holds the "_<enterprise>" part). Matching is case-insensitive because
    GitHub logins are, and a login that is only the suffix is left untouched
    rather than collapsed to an empty username.
    """
    cleaned = (login or "").strip()
    suffix = settings.github_username_suffix.strip()
    if suffix and len(cleaned) > len(suffix) and cleaned.lower().endswith(suffix.lower()):
        return cleaned[: -len(suffix)]
    return cleaned


async def fetch_github_user(token: str) -> dict:
    """Resolve the GitHub account behind a Copilot OAuth token."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "efp-portal",
    }
    async with httpx.AsyncClient(**github_client_kwargs()) as client:
        response = await client.get(GITHUB_USER_API_URL, headers=headers)
    response.raise_for_status()
    data = response.json()
    login = str((data or {}).get("login") or "").strip()
    if not login:
        raise ValueError("GitHub did not return a login for this token")
    return {
        "login": login,
        "username": portal_username_from_github_login(login),
        "name": str(data.get("name") or "").strip(),
        "email": str(data.get("email") or "").strip(),
    }
