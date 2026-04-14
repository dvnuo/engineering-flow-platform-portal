from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.runtime_profile import RuntimeProfile
from app.models.user import User
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.schemas.runtime_profile import dump_runtime_profile_config_json


class RuntimeProfileService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = RuntimeProfileRepository(db)

    @staticmethod
    def default_profile_config() -> dict:
        return {
            "llm": {"provider": "openai", "model": "gpt-4.1"},
            "proxy": {"enabled": False},
            "jira": {"enabled": False, "instances": []},
            "confluence": {"enabled": False, "instances": []},
            "github": {"enabled": False},
            "git": {"user": {}},
            "debug": {"enabled": False, "log_level": "INFO"},
        }

    def list_for_user(self, user) -> list[RuntimeProfile]:
        return self.repo.list_by_owner(user.id)

    def get_for_user(self, user, profile_id: str) -> RuntimeProfile | None:
        return self.repo.get_by_id_for_owner(profile_id, user.id)

    def validate_profile_belongs_to_user(self, user, profile_id: str) -> RuntimeProfile:
        profile = self.get_for_user(user, profile_id)
        if not profile:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RuntimeProfile not found")
        return profile

    def _set_default(self, owner_user_id: int, keep_profile_id: str) -> None:
        profiles = self.repo.list_by_owner(owner_user_id)
        for profile in profiles:
            profile.is_default = profile.id == keep_profile_id
            self.db.add(profile)
        self.db.commit()

    def ensure_user_has_default_profile(self, user: User) -> RuntimeProfile:
        profiles = self.repo.list_by_owner(user.id)
        if not profiles:
            return self.repo.create(
                owner_user_id=user.id,
                name="Default",
                description="Auto-created default runtime profile",
                config_json=dump_runtime_profile_config_json(self.default_profile_config()),
                is_default=True,
            )

        defaults = [p for p in profiles if p.is_default]
        if len(defaults) == 1:
            return defaults[0]

        keep = defaults[0] if defaults else profiles[0]
        self._set_default(user.id, keep.id)
        return self.repo.get_by_id(keep.id)

    def ensure_defaults_for_all_users(self, db: Session | None = None) -> None:
        _ = db
        users = list(self.db.query(User).order_by(User.id.asc()).all())
        for user in users:
            self.ensure_user_has_default_profile(user)

    def repair_legacy_runtime_profiles(self, db: Session | None = None) -> None:
        _ = db
        users = list(self.db.query(User).order_by(User.id.asc()).all())
        if not users:
            return

        fallback_user = next((u for u in users if u.role == "admin"), users[0])
        profiles = self.repo.list_all()
        for profile in profiles:
            bound_agents = list(self.db.query(Agent).filter(Agent.runtime_profile_id == profile.id).order_by(Agent.id.asc()).all())
            by_owner: dict[int, list[Agent]] = {}
            for agent in bound_agents:
                by_owner.setdefault(agent.owner_user_id, []).append(agent)

            owner_ids = list(by_owner.keys())
            if not owner_ids:
                if profile.owner_user_id is None:
                    profile.owner_user_id = fallback_user.id
                    self.db.add(profile)
                    self.db.commit()
                continue

            first_owner = owner_ids[0]
            profile.owner_user_id = first_owner
            self.db.add(profile)
            self.db.commit()

            for owner_id in owner_ids[1:]:
                cloned = RuntimeProfile(
                    owner_user_id=owner_id,
                    name=f"{profile.name} ({owner_id})",
                    description=profile.description,
                    config_json=profile.config_json,
                    revision=profile.revision,
                    is_default=False,
                )
                self.db.add(cloned)
                self.db.commit()
                self.db.refresh(cloned)
                for agent in by_owner[owner_id]:
                    agent.runtime_profile_id = cloned.id
                    self.db.add(agent)
                self.db.commit()

    def create_for_user(self, user, *, name, description, config_json=None, is_default=False) -> RuntimeProfile:
        existing_count = self.repo.count_by_owner(user.id)
        if existing_count == 0:
            is_default = True
        try:
            profile = self.repo.create(
                owner_user_id=user.id,
                name=name,
                description=description,
                config_json=config_json or dump_runtime_profile_config_json(self.default_profile_config()),
                is_default=bool(is_default),
            )
        except IntegrityError as exc:
            self.db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="RuntimeProfile name already exists") from exc

        if profile.is_default:
            self._set_default(user.id, profile.id)
            profile = self.repo.get_by_id(profile.id)
        return profile

    def update_for_user(self, user, profile_id, *, name=None, description=None, config_json=None, is_default=None):
        profile = self.validate_profile_belongs_to_user(user, profile_id)
        before_config = profile.config_json
        if name is not None:
            profile.name = name
        if description is not None:
            profile.description = description
        if config_json is not None:
            profile.config_json = config_json
        if is_default is not None:
            profile.is_default = bool(is_default)

        try:
            profile = self.repo.save(profile)
        except IntegrityError as exc:
            self.db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="RuntimeProfile name already exists") from exc

        if is_default is True:
            self._set_default(user.id, profile.id)
            profile = self.repo.get_by_id(profile.id)
        elif is_default is False and profile.is_default is False:
            # avoid breaking invariant
            if not self.repo.get_default_for_owner(user.id):
                self._set_default(user.id, profile.id)
                profile = self.repo.get_by_id(profile.id)

        config_changed = before_config != profile.config_json
        if config_changed:
            profile.revision = (profile.revision or 0) + 1
            profile = self.repo.save(profile)
        return profile, config_changed

    def delete_for_user(self, user, profile_id):
        profile = self.validate_profile_belongs_to_user(user, profile_id)
        owner_profiles = self.repo.list_by_owner(user.id)
        if len(owner_profiles) <= 1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot delete the last RuntimeProfile")
        if self.repo.count_bound_agents(profile.id) > 0:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="RuntimeProfile is still referenced by agents")

        promote_target = None
        if profile.is_default:
            promote_target = next((p for p in owner_profiles if p.id != profile.id), None)

        self.repo.delete(profile)
        if promote_target:
            fresh = self.repo.get_by_id(promote_target.id)
            if fresh:
                self._set_default(user.id, fresh.id)
        return profile
