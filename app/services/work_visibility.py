"""Who may see an assistant, a task or a delegation rule: its owner, a system
administrator, the owner's group when it is public, everyone when it is the
platform's.

All three tables carry ``visibility``:

- ``private`` (the default): the owner and system administrators only;
- ``public``: every member of the owner's group (``users.group_id``). A
  public assistant runs with its owner's connectors, their group's shared
  credentials included, so sharing it stops at the group;
- ``platform``: every signed-in member. Assistants only, and only a system
  administrator sets it (a help assistant for everyone, say).

The lists, the detail routes, the chat proxy and the overview service all
ask this module instead of each spelling the rule out. A task made by a
delegation rule takes the rule's visibility; a follow-up or a rerun keeps
its task's.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.orm import object_session

VISIBILITY_PRIVATE = "private"
VISIBILITY_PUBLIC = "public"
VISIBILITY_PLATFORM = "platform"
# What a task or a delegation rule may be.
VISIBILITIES = (VISIBILITY_PRIVATE, VISIBILITY_PUBLIC)
# What an assistant may be; ``platform`` is a system administrator's to give.
AGENT_VISIBILITIES = (VISIBILITY_PRIVATE, VISIBILITY_PUBLIC, VISIBILITY_PLATFORM)
SHARED_VISIBILITIES = (VISIBILITY_PUBLIC, VISIBILITY_PLATFORM)


def normalize_visibility(value: Any, *, default: str = VISIBILITY_PRIVATE) -> str:
    """``private`` or ``public``; empty means ``default``; anything else is a ValueError."""
    cleaned = str(value or "").strip().lower()
    if not cleaned:
        return default
    if cleaned not in VISIBILITIES:
        raise ValueError("visibility must be 'private' or 'public'")
    return cleaned


def is_admin(user: Any) -> bool:
    """A system administrator; a group administrator sees no one's private work."""
    return str(getattr(user, "role", "") or "").strip().lower() == "admin"


def _default_group_id_expr():
    from app.models.group import Group

    return select(Group.id).where(Group.is_default.is_(True)).order_by(Group.id.asc()).limit(1).scalar_subquery()


def _group_key_expr(group_id_column):
    """A member's group in SQL: theirs, else the default group's id, else 0 (a database with no group)."""

    return func.coalesce(group_id_column, _default_group_id_expr(), 0)


def _viewer_group_key_expr(user: Any):
    group_id = getattr(user, "group_id", None)
    return literal(group_id) if group_id is not None else func.coalesce(_default_group_id_expr(), 0)


def same_group_owner_ids(user: Any):
    """A SELECT of the ids of every account in ``user``'s group."""

    from app.models.user import User

    return select(User.id).where(_group_key_expr(User.group_id) == _viewer_group_key_expr(user))


def in_same_group(db: Any, owner_user_id: Any, user: Any) -> bool:
    """Whether the account ``owner_user_id`` is in ``user``'s group."""

    if db is None or owner_user_id is None or user is None:
        return False
    from app.models.group import Group
    from app.models.user import User

    owner = db.get(User, owner_user_id)
    if owner is None:
        return False
    if owner.group_id is not None and owner.group_id == getattr(user, "group_id", None):
        return True
    default_id = db.scalar(select(Group.id).where(Group.is_default.is_(True)).order_by(Group.id.asc()).limit(1))

    def key(value):
        return value if value is not None else default_id

    return key(owner.group_id) == key(getattr(user, "group_id", None))


def can_see(row: Any, user: Any, db: Any = None) -> bool:
    """Whether ``user`` may read ``row`` (anything with ``owner_user_id`` and ``visibility``).

    ``db`` defaults to the row's own session; a public row outside any
    session is not shown, since its owner's group cannot be told.
    """
    if user is None or row is None:
        return False
    if is_admin(user):
        return True
    if getattr(row, "owner_user_id", None) == getattr(user, "id", None):
        return True
    visibility = str(getattr(row, "visibility", "") or VISIBILITY_PRIVATE).strip().lower()
    if visibility == VISIBILITY_PLATFORM:
        return True
    if visibility != VISIBILITY_PUBLIC:
        return False
    if db is None:
        try:
            db = object_session(row)
        except Exception:
            db = None
    return in_same_group(db, getattr(row, "owner_user_id", None), user)


def visibility_clause(model: Any, user: Any):
    """A SQLAlchemy filter selecting the rows ``user`` may read; None for a system administrator (no filter)."""
    if user is None:
        return model.visibility == VISIBILITY_PLATFORM
    if is_admin(user):
        return None
    return or_(
        model.owner_user_id == getattr(user, "id", None),
        model.visibility == VISIBILITY_PLATFORM,
        and_(model.visibility == VISIBILITY_PUBLIC, model.owner_user_id.in_(same_group_owner_ids(user))),
    )
