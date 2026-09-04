import json
import logging

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
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
    parse_runtime_profile_source,
    PROFILE_SOURCE_BLANK,
    PROFILE_SOURCE_DEFAULT_CONNECTIONS,
    PROFILE_SOURCE_PROFILE,
    PROFILE_SOURCE_PROFILE_PREFIX,
)
from app.services.runtime_profile_config_policy import canonicalize_portal_runtime_profile_config

logger = logging.getLogger(__name__)

# The description the portal writes on a member's first profile. It is shown to
# whoever opens the create-profile picker, so it says what happened in plain
# words rather than naming the table it lives in. The legacy set is what older
# builds wrote: recognised so the picker can drop it instead of repeating
# machine jargon at a member who never typed it, and so no row has to be
# rewritten to get the tidier reading.
DEFAULT_PROFILE_DESCRIPTION = "Set up for you when you joined"
LEGACY_DEFAULT_PROFILE_DESCRIPTIONS = frozenset({"Auto-created default runtime profile"})


def _as_sentence(text: str) -> str:
    """Close a member-written description so the line after it does not run on.

    Descriptions are free text and most people leave the full stop off.
    """
    cleaned = (text or "").strip()
    if cleaned and cleaned[-1] not in ".!?:":
        cleaned += "."
    return cleaned


# Section keys are the config's own vocabulary; the dialog says them the way the
# Connections form does.
SEED_SECTION_LABELS = {
    "llm": "LLM model",
    "mobile-auto": "BrowserStack",
    "jira": "Jira",
    "confluence": "Confluence",
    "github": "GitHub",
    "jenkins": "Jenkins",
    "proxy": "Proxy",
    "aws": "AWS",
    "git": "Git",
    "debug": "Debug",
}


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
            "mobile-auto": {
                "enabled": False,
            },
            "git": {"user": {}},
            "debug": {"enabled": False, "log_level": "INFO"},
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

    def list_for_user(self, user) -> list[RuntimeProfile]:
        return self.repo.list_by_owner_newest_first(user.id)

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

    def _seeded_default_config_json(self) -> str:
        """Build a profile config from the admin-maintained Default Connections.

        The seed carries connection shape (URLs, API versions, project keys) and
        whatever shared credentials the admin chose to fill in, so the member
        lands on a profile already pointing at the right Jira/Confluence and
        supplies only what was left blank. A missing or unreadable seed falls
        back to the empty default rather than blocking sign-in -- this runs on
        the registration path, where an exception would lock a member out.
        """
        try:
            from app.services.runtime_profile_seed_service import RuntimeProfileSeedService

            seed = RuntimeProfileSeedService(self.db).get_seed()
        except Exception:  # pragma: no cover - seed must never block onboarding
            logger.warning("Falling back to an empty default profile; seed could not be read", exc_info=True)
            seed = {}
        if not seed:
            return self.normalize_persisted_config_json(None)
        return self.normalize_persisted_config_json(json.dumps(seed))

    def ensure_user_has_default_profile(self, user: User) -> RuntimeProfile:
        profiles = self.repo.list_by_owner(user.id)
        if not profiles:
            return self.repo.create(
                owner_user_id=user.id,
                name="Default",
                description=DEFAULT_PROFILE_DESCRIPTION,
                config_json=self._seeded_default_config_json(),
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

    def config_json_for_source(self, user, source: str | None) -> str:
        """Resolve a create-source into the config the new profile starts with.

        Copying happens here rather than in the browser because a profile's
        credentials never leave the server in readable form. "Copy from my
        profile" therefore goes through validate_profile_belongs_to_user, which
        404s on anyone else's profile -- the copy can only ever be of something
        the member already holds.
        """
        kind, profile_id = parse_runtime_profile_source(source)
        if kind == PROFILE_SOURCE_DEFAULT_CONNECTIONS:
            return self._seeded_default_config_json()
        if kind == PROFILE_SOURCE_PROFILE:
            origin = self.validate_profile_belongs_to_user(user, profile_id)
            return self.normalize_persisted_config_json(origin.config_json)
        return self.normalize_persisted_config_json(None)

    def creation_sources(self, user) -> list[dict]:
        """What the "Start this profile with" picker offers, in the order shown.

        Written for whoever opens the dialog, not for whoever built it. Nobody
        outside Administration has seen the words "Default Connections", so the
        option is named after where it came from -- an admin prepared it -- and
        not after a scope the portal cannot know: an installation may serve one
        team or a whole company. It says which services it covers and whether
        it arrives with sign-in details, which is the one thing that decides how
        much work is left for whoever picked it.

        The shared setup is listed first, and the client selects whatever comes
        first, so the ordering here is the recommendation. It appears only when
        an admin has actually filled it in: an option that silently produces an
        empty profile would be worse than no option at all.
        """
        shared: list[dict] = []
        try:
            from app.services.runtime_profile_seed_service import (
                find_secret_fields,
                RuntimeProfileSeedService,
            )

            seed = RuntimeProfileSeedService(self.db).get_seed()
        except Exception:  # pragma: no cover - the dialog must still open
            logger.warning("Could not read the connection seed for the source list", exc_info=True)
            seed = {}
        if seed:
            named = ", ".join(sorted(SEED_SECTION_LABELS.get(key, key) for key in seed))
            sign_in = (
                "Shared sign-in details are included, so there may be nothing left to enter."
                if find_secret_fields(seed)
                else "You still add your own sign-in details."
            )
            shared.append(
                {
                    "value": PROFILE_SOURCE_DEFAULT_CONNECTIONS,
                    "label": "The setup your admin prepared",
                    # Leading with the verb rather than the list keeps the
                    # sentence right whether the admin seeded one service or six.
                    # The label already credits the admin, so this does not.
                    "detail": f"Already set up for you: {named}. {sign_in}",
                    "group": "start",
                }
            )

        sources: list[dict] = shared + [
            {
                "value": PROFILE_SOURCE_BLANK,
                "label": "Nothing - I'll set it up myself",
                "detail": "Every service starts empty, for you to fill in.",
                "group": "start",
            }
        ]

        for profile in self.list_for_user(user):
            # "(Default)" next to a name invites the question "default what?".
            # The answer belongs in the description line, where there is room
            # for it.
            description = (profile.description or "").strip()
            if description in LEGACY_DEFAULT_PROFILE_DESCRIPTIONS:
                description = ""
            notes = [_as_sentence(description)] if description else []
            if profile.is_default:
                notes.append("This is your current default.")
            notes.append("An exact copy, including any sign-in details you saved.")
            sources.append(
                {
                    "value": f"{PROFILE_SOURCE_PROFILE_PREFIX}{profile.id}",
                    "label": f"A copy of {profile.name}",
                    "detail": " ".join(notes),
                    "group": "copy",
                }
            )
        return sources

    def create_for_user(self, user, *, name, description, config_json=None, is_default=False) -> RuntimeProfile:
        existing_count = self.repo.count_by_owner(user.id)
        if existing_count == 0:
            is_default = True
        try:
            profile = self.repo.create(
                owner_user_id=user.id,
                name=name,
                description=description,
                config_json=self.normalize_persisted_config_json(config_json),
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
            profile.config_json = self.normalize_persisted_config_json(config_json)
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
