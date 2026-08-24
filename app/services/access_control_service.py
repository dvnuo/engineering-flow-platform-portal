import re

from sqlalchemy.orm import Session

from app.config import Settings
from app.repositories.user_allowlist_repo import UserAllowlistRepository, normalize_username
from app.repositories.user_repo import UserRepository
from app.services.auth_service import hash_password


ALLOWED_USER_ROLES = {"admin", "user", "viewer"}


def configured_allowlist_usernames(raw_value: str) -> list[str]:
    seen: set[str] = set()
    usernames: list[str] = []
    for value in re.split(r"[,;\n]", str(raw_value or "")):
        normalized = normalize_username(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            usernames.append(normalized)
    return usernames


class AccessControlService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.allowlist = UserAllowlistRepository(db)

    def is_username_allowed(self, username: str) -> bool:
        return self.allowlist.get_active_by_username(username) is not None

    def ensure_configured_access(self, settings: Settings):
        for username in configured_allowlist_usernames(settings.portal_user_allowlist):
            self.allowlist.ensure(username, role="user", reactivate=True, update_role=False)

        admin_username = normalize_username(settings.bootstrap_admin_username)
        if not admin_username:
            return None

        admin_user = self.users.get_by_username_case_insensitive(admin_username)
        if not admin_user and not settings.bootstrap_admin_password:
            return None

        if not admin_user:
            admin_user = self.users.create(
                admin_username,
                hash_password(settings.bootstrap_admin_password),
                role="admin",
            )
        elif admin_user.role != "admin":
            if not settings.bootstrap_admin_password:
                return None
            # If a non-admin account already claimed the configured name,
            # rotate it to the deployment-controlled password before promotion.
            self.users.update_password(admin_user, hash_password(settings.bootstrap_admin_password))
            admin_user = self.users.update_access(admin_user, role="admin", is_active=True)
        elif not admin_user.is_active:
            admin_user = self.users.update_access(admin_user, is_active=True)
        self.allowlist.ensure(admin_username, role="admin", reactivate=True)
        return admin_user

    def is_effective_admin(self, user) -> bool:
        return bool(
            user
            and user.role == "admin"
            and user.is_active
            and self.is_username_allowed(user.username)
        )

    def count_effective_admins(self) -> int:
        return sum(1 for user in self.users.list_all() if self.is_effective_admin(user))
