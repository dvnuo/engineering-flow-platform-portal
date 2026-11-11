"""Who administers what: system administrators, group administrators, members.

- ``admin``: a system administrator. Everything on every group, as before
  groups existed. Every ``role == "admin"`` check in the Portal still means
  exactly this, so a new role never inherits one of them by accident.
- ``group_admin``: administers their own group (``users.group_id``): its
  Default connectors, its allowlist, and its members' roles up to group
  administrator. Nothing outside the group, and no member's private work.
- ``user``: a member.

The rules live here so the API and the pages ask one place.
"""

from __future__ import annotations

from typing import Any

ROLE_SYSTEM_ADMIN = "admin"
ROLE_GROUP_ADMIN = "group_admin"
ROLE_MEMBER = "user"
ROLES = (ROLE_MEMBER, ROLE_GROUP_ADMIN, ROLE_SYSTEM_ADMIN)

# What a group administrator may make someone in their own group.
GROUP_ADMIN_ASSIGNABLE_ROLES = (ROLE_MEMBER, ROLE_GROUP_ADMIN)


def role_of(user: Any) -> str:
    return str(getattr(user, "role", "") or "").strip().lower()


def is_system_admin(user: Any) -> bool:
    return user is not None and role_of(user) == ROLE_SYSTEM_ADMIN


def is_group_admin(user: Any) -> bool:
    return user is not None and role_of(user) == ROLE_GROUP_ADMIN


def is_administrator(user: Any) -> bool:
    """A system or a group administrator: someone who sees Administration."""

    return is_system_admin(user) or is_group_admin(user)


def group_id_of(user: Any, default_group_id: int | None) -> int | None:
    """The member's group; an account with none is in the default group."""

    group_id = getattr(user, "group_id", None)
    return group_id if group_id is not None else default_group_id


def administers_group(user: Any, group_id: int | None, default_group_id: int | None) -> bool:
    if is_system_admin(user):
        return True
    if not is_group_admin(user):
        return False
    target = group_id if group_id is not None else default_group_id
    return target is not None and group_id_of(user, default_group_id) == target


def assignable_roles(actor: Any) -> tuple[str, ...]:
    if is_system_admin(actor):
        return ROLES
    if is_group_admin(actor):
        return GROUP_ADMIN_ASSIGNABLE_ROLES
    return ()


def can_manage_member(actor: Any, target_role: str, target_group_id: int | None, default_group_id: int | None) -> bool:
    """Whether ``actor`` may change the access of someone with ``target_role`` in ``target_group_id``.

    A group administrator manages members and group administrators of their
    own group, never a system administrator.
    """

    if is_system_admin(actor):
        return True
    if not is_group_admin(actor):
        return False
    if str(target_role or "").strip().lower() not in GROUP_ADMIN_ASSIGNABLE_ROLES:
        return False
    return administers_group(actor, target_group_id, default_group_id)


__all__ = [
    "GROUP_ADMIN_ASSIGNABLE_ROLES",
    "ROLES",
    "ROLE_GROUP_ADMIN",
    "ROLE_MEMBER",
    "ROLE_SYSTEM_ADMIN",
    "administers_group",
    "assignable_roles",
    "can_manage_member",
    "group_id_of",
    "is_administrator",
    "is_group_admin",
    "is_system_admin",
    "role_of",
]
