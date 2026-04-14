from __future__ import annotations

from app.models.runtime_profile import RuntimeProfile
from app.repositories.runtime_profile_repo import RuntimeProfileRepository


DEFAULT_RUNTIME_PROFILE_NAME = "Default Runtime"
DEFAULT_RUNTIME_PROFILE_DESCRIPTION = "Auto-provisioned default runtime profile"


class RuntimeProfileService:
    def ensure_user_has_default_profile(self, db, user_id: int) -> RuntimeProfile:
        repo = RuntimeProfileRepository(db)
        profiles = repo.list_by_owner(user_id)
        if not profiles:
            return repo.create(
                owner_user_id=user_id,
                name=DEFAULT_RUNTIME_PROFILE_NAME,
                description=DEFAULT_RUNTIME_PROFILE_DESCRIPTION,
                config_json="{}",
                revision=1,
                is_default=True,
            )

        defaults = [profile for profile in profiles if profile.is_default]
        if len(defaults) == 1:
            return defaults[0]

        chosen = sorted(profiles, key=lambda p: (p.created_at, p.id))[0]
        for profile in profiles:
            profile.is_default = profile.id == chosen.id
            db.add(profile)
        db.commit()
        db.refresh(chosen)
        return chosen

    def create_profile_for_user(
        self,
        db,
        user_id: int,
        *,
        name: str,
        description: str | None,
        config_json: str = "{}",
    ) -> RuntimeProfile:
        repo = RuntimeProfileRepository(db)
        is_first = repo.count_by_owner(user_id) == 0
        return repo.create(
            owner_user_id=user_id,
            name=name,
            description=description,
            config_json=config_json,
            revision=1,
            is_default=is_first,
        )

    def set_default_profile(self, db, user_id: int, profile_id: str) -> RuntimeProfile:
        repo = RuntimeProfileRepository(db)
        target = repo.get_by_id_for_owner(profile_id, user_id)
        if not target:
            raise ValueError("RuntimeProfile not found")

        profiles = repo.list_by_owner(user_id)
        for profile in profiles:
            profile.is_default = profile.id == target.id
            db.add(profile)
        db.commit()
        db.refresh(target)
        return target

    def delete_profile_for_user(self, db, user_id: int, profile_id: str) -> tuple[bool, str | None]:
        repo = RuntimeProfileRepository(db)
        profile = repo.get_by_id_for_owner(profile_id, user_id)
        if not profile:
            return False, "RuntimeProfile not found"

        profiles = repo.list_by_owner(user_id)
        if len(profiles) <= 1:
            return False, "Each user must keep at least one runtime profile."

        was_default = bool(profile.is_default)
        repo.delete(profile)

        if was_default:
            successor = repo.first_by_owner(user_id)
            if successor:
                self.set_default_profile(db, user_id, successor.id)

        return True, None

    def resolve_default_profile_for_user(self, db, user_id: int) -> RuntimeProfile:
        return self.ensure_user_has_default_profile(db, user_id)
