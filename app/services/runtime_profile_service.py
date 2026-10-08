import json
import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.runtime_profile import RuntimeProfile
from app.models.user import User
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.services.connector_registry import SETTINGS_CONNECTORS
from app.contracts.llm_catalog import (
    DEFAULT_CONTEXT_SIZE,
    DEFAULT_COPILOT_MODEL,
    DEFAULT_REASONING_EFFORT,
    PROVIDER_MODELS,
    normalize_provider,
)
from app.schemas.runtime_profile import (
    dump_runtime_profile_config_json,
    parse_runtime_profile_config_json,
)
from app.services.connector_defaults_service import (
    MODE_CUSTOM,
    MODE_SYSTEM,
    backfill_connector_modes,
    connector_mode,
    dump_connector_modes,
    effective_config,
    managed_slice,
    managed_specs,
    sanitized_seed,
    stored_connector_modes,
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
                "model": DEFAULT_COPILOT_MODEL,
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

    def read_seed(self) -> dict:
        """The admin-maintained Default connectors, or ``{}`` when they cannot be read.

        Read on the registration path and on every settings render, where an
        exception would lock a member out, so it never raises.
        """
        try:
            from app.services.runtime_profile_seed_service import RuntimeProfileSeedService

            return RuntimeProfileSeedService(self.db).get_seed()
        except Exception:  # pragma: no cover - seed must never block onboarding
            logger.warning("Falling back to empty Default connectors; seed could not be read", exc_info=True)
            return {}

    def _seeded_default_config_json(self) -> str:
        """Build a member's first settings from the admin-maintained defaults.

        Only what no connector mode governs is copied: the model provider's
        defaults and shared key. Everything else a new member gets by
        following the Default connectors (``effective_config_for``), so a
        later change by the administrator reaches them without a copy going
        stale.
        """
        seed = self.read_seed()
        if not seed:
            return self.normalize_persisted_config_json(None)
        managed = {section for spec in SETTINGS_CONNECTORS for section in spec.managed_sections}
        own = {section: value for section, value in seed.items() if section not in managed}
        return self.normalize_persisted_config_json(json.dumps(own))

    # ----------------------------------------------------------- connector modes

    def connector_modes(self, profile: RuntimeProfile) -> dict[str, str]:
        """``{connector type: mode}`` as stored; absent types follow the Default connectors."""

        return stored_connector_modes(getattr(profile, "connector_modes_json", None))

    def effective_config_for(self, profile: RuntimeProfile) -> dict:
        """The settings the member's assistants get: the row, with every connector
        in system mode read from the current Default connectors."""

        return effective_config(
            parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True),
            self.connector_modes(profile),
            self.read_seed(),
        )

    def customize_connector(self, profile: RuntimeProfile, spec) -> tuple[RuntimeProfile, bool]:
        """Switch ``spec`` to the member's own values.

        The row keeps the values the member had before they last followed the
        system default, so those come back; a connector never customized
        starts from the current Default connectors. Nothing the assistants
        see changes, so the revision stays. Returns ``(profile, restored)``.
        """

        modes = self.connector_modes(profile)
        if connector_mode(spec, modes) == MODE_CUSTOM:
            return profile, False
        config = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
        restored = bool(managed_slice(spec, config))
        if not restored:
            for section, value in managed_slice(spec, sanitized_seed(self.read_seed())).items():
                config[section] = value
            profile.config_json = self.normalize_persisted_config_json(dump_runtime_profile_config_json(config))
        modes[spec.type] = MODE_CUSTOM
        profile.connector_modes_json = dump_connector_modes(modes)
        return self.repo.save(profile), restored

    def follow_system_defaults(self, profile: RuntimeProfile, spec) -> tuple[RuntimeProfile, bool]:
        """Switch ``spec`` back to the administrator's Default connectors.

        The member's own values stay in the row, unused, in case they switch
        back. Returns ``(profile, changed)``: whether the settings the
        assistants get differ, in which case the revision is bumped so the
        caller rolls the change out.
        """

        modes = self.connector_modes(profile)
        if connector_mode(spec, modes) == MODE_SYSTEM:
            return profile, False
        before = self.effective_config_for(profile)
        modes.pop(spec.type, None)
        profile.connector_modes_json = dump_connector_modes(modes)
        # Values that are just the Default connectors' own are not worth
        # parking: customizing again then starts from the defaults as they
        # are at that time, not from a stale copy.
        config = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
        if managed_slice(spec, config) == managed_slice(spec, sanitized_seed(self.read_seed())):
            for section in spec.managed_sections:
                config.pop(section, None)
            profile.config_json = self.normalize_persisted_config_json(dump_runtime_profile_config_json(config))
        changed = self.effective_config_for(profile) != before
        if changed:
            profile.revision = (profile.revision or 0) + 1
        return self.repo.save(profile), changed

    def apply_connector_modes(self, profile: RuntimeProfile, overrides: dict | None) -> RuntimeProfile:
        """Set modes explicitly (API clients): ``{type: "system" | "custom"}``; unknown types and values are ignored."""

        if not overrides:
            return profile
        modes = self.connector_modes(profile)
        known = {spec.type for spec in managed_specs()}
        for key, value in dict(overrides).items():
            if str(key) not in known:
                continue
            if value == MODE_CUSTOM:
                modes[str(key)] = MODE_CUSTOM
            elif value == MODE_SYSTEM:
                modes.pop(str(key), None)
        profile.connector_modes_json = dump_connector_modes(modes)
        return self.repo.save(profile)

    def backfill_connector_modes(self) -> int:
        """Classify rows from before modes existed (``connector_modes_json`` NULL).

        A connector whose row values equal the current Default connectors
        follows them from now on and its copy leaves the row; one whose
        values differ stays the member's own. Nothing an assistant sees
        changes, so no revision moves. Returns the number of rows classified.
        """

        seed = self.read_seed()
        count = 0
        for profile in self.repo.list_all():
            if profile.connector_modes_json is not None:
                continue
            config = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
            modes = backfill_connector_modes(config, seed)
            for spec in SETTINGS_CONNECTORS:
                if spec.managed_sections and connector_mode(spec, modes) == MODE_SYSTEM:
                    for section in spec.managed_sections:
                        config.pop(section, None)
            profile.config_json = self.normalize_persisted_config_json(dump_runtime_profile_config_json(config))
            profile.connector_modes_json = dump_connector_modes(modes)
            self.db.add(profile)
            count += 1
        if count:
            self.db.commit()
            logger.info("Classified %d settings rows into connector modes", count)
        return count

    def get_for_owner(self, owner_user_id: int) -> RuntimeProfile | None:
        return self.repo.get_for_owner(owner_user_id)

    def get_or_create_for_user(self, user: User) -> RuntimeProfile:
        """The member's one settings row, created from the seed on first use."""

        profile = self.repo.get_for_owner(user.id)
        if profile:
            return profile
        try:
            return self.repo.create(
                owner_user_id=user.id,
                name=DEFAULT_PROFILE_NAME,
                description=DEFAULT_PROFILE_DESCRIPTION,
                config_json=self._seeded_default_config_json(),
                # Every connector follows the Default connectors to begin with.
                connector_modes_json=dump_connector_modes({}),
                is_default=True,
            )
        except IntegrityError:
            # Another request created it first (uq_runtime_profiles_owner).
            self.db.rollback()
            profile = self.repo.get_for_owner(user.id)
            if profile is None:
                raise
            return profile

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
        new_config_json = self._mark_written_connectors_custom(profile, new_config_json)
        if new_config_json == (profile.config_json or ""):
            return profile, False
        profile.config_json = new_config_json
        profile.revision = (profile.revision or 0) + 1
        return self.repo.save(profile), True

    def _mark_written_connectors_custom(self, profile: RuntimeProfile, new_config_json: str) -> str:
        """New values written for a connector that follows the Default connectors make it the member's own.

        Only sections this write changes count, so values parked in the row
        from before the member last followed the system default stay parked.
        Writing the Default connectors' own values is not customizing: a
        client that reads the effective document and writes it back leaves
        every mode as it was, and no copy of the defaults enters the row.
        Returns the config JSON to store.
        """

        if profile.connector_modes_json is None:
            return new_config_json  # not classified yet: every connector is the member's own already
        old = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
        new = parse_runtime_profile_config_json(new_config_json, fallback_to_empty=True)
        modes = self.connector_modes(profile)
        seed_config = sanitized_seed(self.read_seed())
        modes_changed = config_changed = False
        for spec in managed_specs():
            if connector_mode(spec, modes) != MODE_SYSTEM:
                continue
            written = managed_slice(spec, new)
            if not written or written == managed_slice(spec, old):
                continue
            if written == managed_slice(spec, seed_config):
                for section in spec.managed_sections:
                    if section in new:
                        del new[section]
                config_changed = True
                continue
            modes[spec.type] = MODE_CUSTOM
            modes_changed = True
        if modes_changed:
            profile.connector_modes_json = dump_connector_modes(modes)
        if config_changed:
            return self.normalize_persisted_config_json(dump_runtime_profile_config_json(new))
        return new_config_json
