"""Groups: the team a member belongs to and the Default connectors it gives them.

Every member is in exactly one group (``users.group_id``; NULL is the
default group). The Default connectors a member follows are their group's:
the platform's (``RuntimeProfileSeedService``) with the connectors the group
sets itself replaced by its own values (``connector_defaults_service.group_seed``).

A member changing group keeps their own connectors and follows the new
group's defaults from then on. The model provider is the exception that
needs care: a member's first row copies their group's model defaults, shared
credentials included, so a change of group swaps the old group's for the new
one's (``member_llm_after_group_change``) instead of letting a member keep
the account the old group pays for. Whatever the move changes for the
assistants is rolled out by the caller (``connector_defaults_sync.rollout_profiles``).
"""
from __future__ import annotations

import copy
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models.group import Group
from app.models.user import User
from app.repositories.audit_repo import AuditRepository
from app.repositories.group_repo import GroupRepository
from app.repositories.user_allowlist_repo import UserAllowlistRepository
from app.schemas.runtime_profile import ALLOWED_RUNTIME_PROFILE_SECTIONS, parse_runtime_profile_config_json
from app.services.authorization import ROLE_GROUP_ADMIN, ROLE_MEMBER
from app.services.connector_defaults_service import (
    GROUP_CHOICE_CUSTOM,
    GROUP_CHOICE_NONE,
    GROUP_CHOICE_PLATFORM,
    MODE_CUSTOM,
    changed_connector_types,
    dump_connector_modes,
    group_sections,
    group_seed,
    group_specs,
    parse_group_modes,
)
from app.services.runtime_profile_config_policy import canonicalize_portal_runtime_profile_config

logger = logging.getLogger(__name__)

GROUP_NAME_MAX_LENGTH = 64

# The model provider's shared credentials, as a group's (or the platform's)
# Default connectors carry them.
LLM_CREDENTIAL_PATHS: tuple[tuple[str, ...], ...] = (
    ("api_key",),
    ("ai_platform", "auth", "username"),
    ("ai_platform", "auth", "password"),
    ("ai_platform", "auth", "usercase"),
)


def _get_path(source: Mapping[str, Any] | None, path: tuple[str, ...]) -> Any:
    value: Any = source
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _set_path(target: dict, path: tuple[str, ...], value: Any) -> None:
    node = target
    for key in path[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[path[-1]] = value


def _pop_path(target: dict, path: tuple[str, ...]) -> None:
    parents: list[tuple[dict, str]] = []
    node: Any = target
    for key in path[:-1]:
        if not isinstance(node, dict) or not isinstance(node.get(key), dict):
            return
        parents.append((node, key))
        node = node[key]
    if isinstance(node, dict):
        node.pop(path[-1], None)
    # Leave no empty ai_platform / auth shells behind.
    for parent, key in reversed(parents):
        if parent.get(key) == {}:
            parent.pop(key, None)


def _llm_settings(llm: Mapping[str, Any] | None) -> dict:
    """The model provider's values that are not credentials and not the step limit."""

    result = copy.deepcopy(dict(llm or {})) if isinstance(llm, Mapping) else {}
    for path in LLM_CREDENTIAL_PATHS:
        _pop_path(result, path)
    result.pop("max_steps", None)
    return result


def member_llm_after_group_change(
    member_llm: Mapping[str, Any] | None,
    old_defaults: Mapping[str, Any] | None,
    new_defaults: Mapping[str, Any] | None,
) -> tuple[dict, bool]:
    """The member's model provider once they move from one group to another.

    ``old_defaults`` / ``new_defaults`` are the two groups' model defaults in
    the stored (canonical) shape. Returns ``(llm, needs_attention)``:

    - a credential the member holds because the old group shared it is
      dropped: the new group must not keep paying for, or reading through,
      the old group's account;
    - a member who never changed the old group's model defaults gets the new
      group's, its shared credentials included where they hold none of
      their own (their own Copilot sign-in stays);
    - a member who chose their own model keeps it, and ``needs_attention``
      says when a dropped credential means they have to sign in again.
    """

    member = copy.deepcopy(dict(member_llm or {})) if isinstance(member_llm, Mapping) else {}
    old = old_defaults if isinstance(old_defaults, Mapping) else {}
    new = new_defaults if isinstance(new_defaults, Mapping) else {}
    dropped = False
    for path in LLM_CREDENTIAL_PATHS:
        mine = _get_path(member, path)
        shared = _get_path(old, path)
        if mine and shared and mine == shared:
            _pop_path(member, path)
            dropped = True
    if _llm_settings(member) not in ({}, _llm_settings(old)):
        return member, dropped
    result = _llm_settings(new)
    for path in LLM_CREDENTIAL_PATHS:
        value = _get_path(member, path) or _get_path(new, path)
        if value:
            _set_path(result, path, copy.deepcopy(value))
    if "max_steps" in member:
        # The member's own step limit; one they follow is not in the row.
        result["max_steps"] = member["max_steps"]
    return result, False


def _only_known_sections(config: Mapping[str, Any] | None) -> dict:
    if not isinstance(config, Mapping):
        return {}
    return {key: value for key, value in config.items() if key in ALLOWED_RUNTIME_PROFILE_SECTIONS}


def clean_group_name(value: Any) -> str:
    """A group name as stored: trimmed, inner whitespace collapsed; ValueError when unusable."""

    name = " ".join(str(value or "").split())
    if not name:
        raise ValueError("Give the group a name.")
    if len(name) > GROUP_NAME_MAX_LENGTH:
        raise ValueError(f"A group name may have at most {GROUP_NAME_MAX_LENGTH} characters.")
    return name


@dataclass
class MoveResult:
    """What moving one member changed: the row to roll out, if the assistants' settings moved."""

    user_id: int
    from_group_id: int | None
    to_group_id: int | None
    moved: bool = False
    demoted: bool = False
    profile_id: str | None = None
    llm_needs_attention: bool = False


@dataclass
class GroupDeleteResult:
    moved: list[MoveResult] = field(default_factory=list)
    pending_entries: int = 0

    @property
    def profile_ids(self) -> list[str]:
        return [item.profile_id for item in self.moved if item.profile_id]


class GroupService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = GroupRepository(db)

    # ------------------------------------------------------------ lookups

    def default_group(self) -> Group:
        return self.repo.ensure_default()

    def default_group_id(self) -> int | None:
        group = self.repo.get_default()
        return group.id if group is not None else None

    def group_of(self, user: User | None) -> Group:
        """The member's group: theirs, or the default group (created if missing)."""

        group = self.repo.get_by_id(getattr(user, "group_id", None))
        return group if group is not None else self.default_group()

    def find_group_of(self, user: User | None) -> Group | None:
        """Like ``group_of`` but never writes: None when there is no group at all yet."""

        group = self.repo.get_by_id(getattr(user, "group_id", None))
        return group if group is not None else self.repo.get_default()

    @staticmethod
    def config_of(group: Group | None) -> dict:
        if group is None:
            return {}
        return parse_runtime_profile_config_json(group.config_json, fallback_to_empty=True)

    @staticmethod
    def modes_of(group: Group | None) -> dict[str, str]:
        return parse_group_modes(group.connector_modes_json) if group is not None else {}

    def platform_seed(self) -> dict:
        """The system administrator's Default connectors, or ``{}`` when they cannot be read."""

        try:
            from app.services.runtime_profile_seed_service import RuntimeProfileSeedService

            return RuntimeProfileSeedService(self.db).get_seed()
        except Exception:  # pragma: no cover - the seed must never lock a member out
            logger.warning("Falling back to empty Default connectors; seed could not be read", exc_info=True)
            return {}

    def seed_for_group(self, group: Group | None, platform_seed: Mapping[str, Any] | None = None) -> dict:
        """The Default connectors ``group``'s members follow."""

        platform = self.platform_seed() if platform_seed is None else platform_seed
        if group is None:
            return copy.deepcopy(dict(platform))
        return group_seed(platform, self.config_of(group), self.modes_of(group))

    def seed_for_user_id(self, user_id: int | None) -> dict:
        user = self.db.get(User, user_id) if user_id is not None else None
        return self.seed_for_group(self.find_group_of(user))

    def changes_by_group(self, old_platform: Mapping[str, Any], new_platform: Mapping[str, Any]) -> dict[int | None, list[str]]:
        """``{group id: connectors whose Default connectors changed}`` for a change of the platform's.

        A group that sets a connector itself does not see the platform's
        change to it. ``None`` is for accounts on a database with no group yet.
        """

        result: dict[int | None, list[str]] = {None: changed_connector_types(old_platform, new_platform)}
        for group in self.repo.list_all():
            result[group.id] = changed_connector_types(
                self.seed_for_group(group, old_platform), self.seed_for_group(group, new_platform)
            )
        return result

    # ------------------------------------------------------------ groups

    def create_group(self, name: Any, *, description: Any = None, actor_id: int | None = None) -> Group:
        cleaned = clean_group_name(name)
        if self.repo.get_by_name(cleaned) is not None:
            raise ValueError(f"A group named {cleaned} already exists.")
        group = self.repo.create(cleaned, description=_clean_description(description), updated_by_user_id=actor_id)
        AuditRepository(self.db).create(
            action="create_group",
            target_type="group",
            target_id=str(group.id),
            user_id=actor_id,
            details={"name": group.name},
        )
        return group

    def update_group(self, group: Group, *, name: Any = None, description: Any = None, actor_id: int | None = None) -> Group:
        details: dict[str, Any] = {}
        if name is not None:
            cleaned = clean_group_name(name)
            other = self.repo.get_by_name(cleaned)
            if other is not None and other.id != group.id:
                raise ValueError(f"A group named {cleaned} already exists.")
            if cleaned != group.name:
                details["name"] = {"from": group.name, "to": cleaned}
                group.name = cleaned
        if description is not None:
            group.description = _clean_description(description)
            details["description"] = True
        group.updated_by_user_id = actor_id
        group = self.repo.save(group)
        AuditRepository(self.db).create(
            action="update_group", target_type="group", target_id=str(group.id), user_id=actor_id, details=details
        )
        return group

    def save_group_defaults(
        self,
        group: Group,
        config: Mapping[str, Any] | None,
        choices: Mapping[str, str] | None,
        *,
        actor_id: int | None = None,
    ) -> list[str]:
        """Store what ``group`` sets itself; returns the connectors whose defaults changed for its members.

        ``choices`` is ``{connector type: platform | custom | none}`` (a type
        left out keeps its current choice); ``config`` holds the values of the
        connectors set to ``custom``, in the seed's shape.
        """

        before = self.seed_for_group(group)
        stored = _only_known_sections(self.config_of(group))
        modes = self.modes_of(group)
        posted = _only_known_sections(config)
        for spec in group_specs():
            choice = str((choices or {}).get(spec.type) or "").strip().lower()
            if choice not in (GROUP_CHOICE_PLATFORM, GROUP_CHOICE_CUSTOM, GROUP_CHOICE_NONE):
                continue
            for section in group_sections(spec):
                stored.pop(section, None)
            if choice == GROUP_CHOICE_PLATFORM:
                modes.pop(spec.type, None)
                continue
            modes[spec.type] = MODE_CUSTOM
            if choice == GROUP_CHOICE_CUSTOM:
                for section in group_sections(spec):
                    value = posted.get(section)
                    if isinstance(value, dict) and value:
                        stored[section] = copy.deepcopy(value)
        canonical = canonicalize_portal_runtime_profile_config(stored)
        # Its members receive these as they are, so what the platform's save
        # would refuse is refused here.
        from app.services.runtime_profile_references import seed_config_error

        error = seed_config_error(group_seed(self.platform_seed(), canonical, modes))
        if error:
            raise ValueError(error)
        group.config_json = json.dumps(canonical, ensure_ascii=False, sort_keys=True)
        group.connector_modes_json = dump_connector_modes(modes)
        group.updated_by_user_id = actor_id
        self.repo.save(group)
        changed = changed_connector_types(before, self.seed_for_group(group))
        AuditRepository(self.db).create(
            action="update_group_defaults",
            target_type="group",
            target_id=str(group.id),
            user_id=actor_id,
            details={
                "group": group.name,
                "own_connectors": sorted(modes),
                "changed_connectors": changed,
            },
        )
        return changed

    def delete_group(self, group: Group, target: Group, *, actor_id: int | None = None) -> GroupDeleteResult:
        """Move everyone in ``group`` (accounts and allowlist entries) to ``target``, then delete it."""

        if group.is_default:
            raise ValueError("The default group cannot be deleted.")
        if target.id == group.id:
            raise ValueError("Choose another group for its members.")
        result = GroupDeleteResult()
        for user_id in self.repo.member_ids(group):
            user = self.db.get(User, user_id)
            if user is not None:
                result.moved.append(self.move_member(user, target, actor_id=actor_id, audit=False))
        allowlist = UserAllowlistRepository(self.db)
        for entry in allowlist.list_all():
            if entry.group_id == group.id:
                entry.group_id = target.id
                if entry.role == ROLE_GROUP_ADMIN:
                    entry.role = ROLE_MEMBER
                self.db.add(entry)
                result.pending_entries += 1
        self.db.commit()
        name = group.name
        self.repo.delete(group)
        AuditRepository(self.db).create(
            action="delete_group",
            target_type="group",
            target_id=str(group.id),
            user_id=actor_id,
            details={
                "name": name,
                "moved_to": target.name,
                "members": len(result.moved),
                "pending_entries": result.pending_entries,
            },
        )
        return result

    # ------------------------------------------------------------ members

    def move_member(self, user: User, target: Group, *, actor_id: int | None = None, audit: bool = True) -> MoveResult:
        """Put ``user`` in ``target``; the caller rolls out ``result.profile_id`` when set.

        A group administrator who changes group becomes a member of the new
        one: administering a group is given by that group. The allowlist
        entry follows, so access and group never disagree.
        """

        from app.services.runtime_profile_service import RuntimeProfileService

        source = self.find_group_of(user)
        result = MoveResult(user_id=user.id, from_group_id=source.id if source else None, to_group_id=target.id)
        same_group = source is not None and source.id == target.id
        if same_group and user.group_id == target.id:
            return result

        profiles = RuntimeProfileService(self.db)
        profile = profiles.get_for_owner(user.id)
        before = profiles.effective_config_for(profile) if profile is not None else None
        old_seed = self.seed_for_group(source)

        user.group_id = target.id
        if not same_group and str(user.role or "") == ROLE_GROUP_ADMIN:
            user.role = ROLE_MEMBER
            result.demoted = True
        self.db.add(user)
        entry = UserAllowlistRepository(self.db).get_by_username(user.username)
        if entry is not None:
            entry.group_id = target.id
            entry.role = user.role
            self.db.add(entry)
        self.db.commit()
        result.moved = not same_group

        if profile is not None and not same_group:
            new_seed = self.seed_for_group(target)
            config = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
            llm, attention = member_llm_after_group_change(
                config.get("llm"), _stored_llm(profiles, old_seed), _stored_llm(profiles, new_seed)
            )
            if llm:
                config["llm"] = llm
            else:
                config.pop("llm", None)
            profile.config_json = profiles.normalize_persisted_config_json(json.dumps(config))
            result.llm_needs_attention = attention
            if profiles.effective_config_for(profile) != before:
                profile.revision = (profile.revision or 0) + 1
                result.profile_id = profile.id
            profiles.repo.save(profile)

        if audit and result.moved:
            AuditRepository(self.db).create(
                action="move_member_group",
                target_type="user",
                target_id=str(user.id),
                user_id=actor_id,
                details={
                    "username": user.username,
                    "from_group": source.name if source else None,
                    "to_group": target.name,
                    "demoted": result.demoted,
                    "llm_needs_attention": result.llm_needs_attention,
                },
            )
        return result


def _clean_description(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _stored_llm(profiles, seed: Mapping[str, Any]) -> dict:
    """A seed's model defaults as a member's first row would store them (no step limit)."""

    llm = seed.get("llm") if isinstance(seed, Mapping) else None
    if not isinstance(llm, Mapping) or not llm:
        return {}
    copied = {key: value for key, value in llm.items() if key != "max_steps"}
    stored = parse_runtime_profile_config_json(
        profiles.normalize_persisted_config_json(json.dumps({"llm": copied})), fallback_to_empty=True
    )
    return stored.get("llm") if isinstance(stored.get("llm"), dict) else {}


__all__ = [
    "GROUP_NAME_MAX_LENGTH",
    "GroupDeleteResult",
    "GroupService",
    "LLM_CREDENTIAL_PATHS",
    "MoveResult",
    "clean_group_name",
    "member_llm_after_group_change",
]
