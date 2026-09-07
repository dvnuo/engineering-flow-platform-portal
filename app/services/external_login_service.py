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

from app.models.user import User
from app.repositories.audit_repo import AuditRepository
from app.repositories.user_allowlist_repo import UserAllowlistRepository, normalize_username
from app.repositories.user_repo import UserRepository
from app.schemas.runtime_profile import parse_runtime_profile_config_json
from app.services.access_control_service import ALLOWED_USER_ROLES
from app.services.auth_service import hash_password
from app.services.runtime_profile_service import RuntimeProfileService

logger = logging.getLogger(__name__)

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


def attach_copilot_token_to_default_profile(db: Session, user: User, token: str) -> None:
    """Store the Copilot OAuth token on the member's default runtime profile.

    The default profile is the admin-seeded one (Default Connections), so the
    member inherits the shared Jira/Confluence/GitHub setup and only the LLM
    credential is theirs.
    """
    token = (token or "").strip()
    if not token:
        return
    service = RuntimeProfileService(db)
    profile = service.ensure_user_has_default_profile(user)
    config = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
    llm = dict(config.get("llm") or {}) if isinstance(config, dict) else {}
    llm["provider"] = "github_copilot"
    llm["api_key"] = token
    config = dict(config) if isinstance(config, dict) else {}
    config["llm"] = llm
    profile.config_json = service.normalize_persisted_config_json(json.dumps(config))
    service.repo.save(profile)


async def fetch_github_user(token: str) -> dict:
    """Resolve the GitHub account behind a Copilot OAuth token."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "efp-portal",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(GITHUB_USER_API_URL, headers=headers)
    response.raise_for_status()
    data = response.json()
    login = str((data or {}).get("login") or "").strip()
    if not login:
        raise ValueError("GitHub did not return a login for this token")
    return {
        "login": login,
        "name": str(data.get("name") or "").strip(),
        "email": str(data.get("email") or "").strip(),
    }
