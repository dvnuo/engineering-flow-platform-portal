import json
import logging

from sqlalchemy.orm import Session

from app.models.runtime_profile import RuntimeProfile
from app.models.user import User
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.contracts.llm_catalog import (
    DEFAULT_CONTEXT_SIZE,
    DEFAULT_REASONING_EFFORT,
    PROVIDER_MODELS,
    normalize_provider,
)
from app.schemas.runtime_profile import (
    dump_runtime_profile_config_json,
    parse_runtime_profile_config_json,
)
from app.services.runtime_profile_config_policy import canonicalize_portal_runtime_profile_config

logger = logging.getLogger(__name__)

# The name and description on a member's settings row. Neither is shown any
# more (connectors are the only view of it); they stay because the columns do.
DEFAULT_PROFILE_NAME = "Default"
DEFAULT_PROFILE_DESCRIPTION = "Connector settings"


class RuntimeProfileService:
    # Supported LLM providers -> their selectable models.
    _MANAGED_PROVIDER_MODELS = PROVIDER_MODELS

    def __init__(self, db: Session):
        self.db = db
        self.repo = RuntimeProfileRepository(db)

    @staticmethod
    def normalize_managed_llm_provider(value: str | None) -> str:
        # Normalize a NON-empty value to a supported provider (github_copilot or
        # ai_platform). An empty value stays empty so callers can tell "not set"
        # from a concrete provider.
        provider = str(value or "").strip().lower()
        return normalize_provider(provider) if provider else ""

    @staticmethod
    def managed_model_values_for_provider(provider: str | None) -> tuple[str, ...]:
        normalized = RuntimeProfileService.normalize_managed_llm_provider(provider)
        return RuntimeProfileService._MANAGED_PROVIDER_MODELS.get(normalized, ())

    @staticmethod
    def is_managed_model_allowed(provider: str | None, model: str | None) -> bool:
        trimmed = str(model or "").strip()
        if not trimmed:
            return False
        return trimmed in RuntimeProfileService.managed_model_values_for_provider(provider)

    @staticmethod
    def default_profile_config() -> dict:
        return {
            "llm": {
                "provider": "github_copilot",
                "model": "gpt-5.6-terra",
                "max_tokens": 64000,
                "reasoning_effort": DEFAULT_REASONING_EFFORT,
                "max_context_tokens": DEFAULT_CONTEXT_SIZE,
            },
            "proxy": {"enabled": False},
            "jira": {
                "enabled": False,
                "instances": [],
            },
            "confluence": {
                "enabled": False,
                "instances": [],
            },
            "github": {
                "enabled": False,
            },
            "aws": {
                "enabled": False,
            },
            "nexus": {
                "enabled": False,
                "instances": [],
            },
            "splunk": {
                "enabled": False,
                "instances": [],
            },
            "pgsql": {
                "enabled": False,
                "instances": [],
            },
            "mobile-auto": {
                "enabled": False,
            },
            "git": {"user": {}},
        }

    @staticmethod
    def normalize_persisted_config_json(config_json: str | None) -> str:
        """Return sanitized runtime profile JSON for persistence.

        Persistence must store only raw/sparse Portal-managed snapshot fields,
        never a default-materialized view payload.
        """
        parsed = parse_runtime_profile_config_json(config_json, fallback_to_empty=True)
        parsed = canonicalize_portal_runtime_profile_config(parsed)
        return dump_runtime_profile_config_json(parsed)

    @staticmethod
    def materialize_create_config_json(config_json: str | None) -> str:
        """Backward-compatible alias used by older callsites/tests.

        NOTE: This no longer materializes defaults. It now only normalizes raw
        persisted runtime-profile JSON.
        """
        return RuntimeProfileService.normalize_persisted_config_json(config_json)

    @staticmethod
    def _deep_merge_dicts(base: dict, overlay: dict) -> dict:
        merged: dict = {}
        for key, base_value in base.items():
            if key not in overlay:
                merged[key] = base_value
                continue
            overlay_value = overlay[key]
            if isinstance(base_value, dict) and isinstance(overlay_value, dict):
                merged[key] = RuntimeProfileService._deep_merge_dicts(base_value, overlay_value)
            else:
                merged[key] = overlay_value

        for key, overlay_value in overlay.items():
            if key not in merged:
                merged[key] = overlay_value
        return merged

    @staticmethod
    def merge_with_managed_defaults(config_dict: dict | None) -> dict:
        """Build view-only rendering payload by merging safe managed defaults."""
        overlay = config_dict if isinstance(config_dict, dict) else {}
        return RuntimeProfileService._deep_merge_dicts(RuntimeProfileService.default_profile_config(), overlay)

    def _seeded_default_config_json(self) -> str:
        """Build a member's first settings from the admin-maintained defaults.

        The seed carries connection shape (URLs, API versions, project keys) and
        whatever shared credentials the admin chose to fill in, so the member
        lands on connectors already pointing at the right Jira/Confluence and
        supplies only what was left blank. A missing or unreadable seed falls
        back to the empty default rather than blocking sign-in -- this runs on
        the registration path, where an exception would lock a member out.
        """
        try:
            from app.services.runtime_profile_seed_service import RuntimeProfileSeedService

            seed = RuntimeProfileSeedService(self.db).get_seed()
        except Exception:  # pragma: no cover - seed must never block onboarding
            logger.warning("Falling back to empty connector settings; seed could not be read", exc_info=True)
            seed = {}
        if not seed:
            return self.normalize_persisted_config_json(None)
        return self.normalize_persisted_config_json(json.dumps(seed))

    def get_for_owner(self, owner_user_id: int) -> RuntimeProfile | None:
        return self.repo.get_for_owner(owner_user_id)

    def get_or_create_for_user(self, user: User) -> RuntimeProfile:
        """The member's one settings row, created from the seed on first use."""

        profile = self.repo.get_for_owner(user.id)
        if profile:
            return profile
        return self.repo.create(
            owner_user_id=user.id,
            name=DEFAULT_PROFILE_NAME,
            description=DEFAULT_PROFILE_DESCRIPTION,
            config_json=self._seeded_default_config_json(),
            is_default=True,
        )

    # Older call sites say "default profile"; there is only one now.
    ensure_user_has_default_profile = get_or_create_for_user

    def bind_unassigned_agents(self, user: User, profile: RuntimeProfile) -> int:
        """Point the member's assistants that have no settings row at ``profile``."""

        from app.models.agent import Agent

        agents = list(
            self.db.query(Agent)
            .filter(Agent.owner_user_id == user.id, Agent.runtime_profile_id.is_(None))
            .all()
        )
        for agent in agents:
            agent.runtime_profile_id = profile.id
            self.db.add(agent)
        if agents:
            self.db.commit()
        return len(agents)

    def ensure_defaults_for_all_users(self, db: Session | None = None) -> None:
        _ = db
        users = list(self.db.query(User).order_by(User.id.asc()).all())
        for user in users:
            profile = self.get_or_create_for_user(user)
            self.bind_unassigned_agents(user, profile)

    def sanitize_all_persisted_runtime_profiles(self) -> int:
        updated_count = 0
        profiles = self.repo.list_all()
        for profile in profiles:
            sanitized = self.normalize_persisted_config_json(profile.config_json)
            if sanitized == (profile.config_json or ""):
                continue
            profile.config_json = sanitized
            self.db.add(profile)
            updated_count += 1
        if updated_count:
            self.db.commit()
        return updated_count

    def save_config(self, profile: RuntimeProfile, config: dict | str) -> tuple[RuntimeProfile, bool]:
        """Persist ``config`` and bump the revision when it actually changed.

        Returns ``(profile, changed)``. Saving the same settings again is a
        no-op, so it never marks assistants as needing a restart.
        """

        raw = config if isinstance(config, str) else dump_runtime_profile_config_json(config)
        new_config_json = self.normalize_persisted_config_json(raw)
        if new_config_json == (profile.config_json or ""):
            return profile, False
        profile.config_json = new_config_json
        profile.revision = (profile.revision or 0) + 1
        return self.repo.save(profile), True
