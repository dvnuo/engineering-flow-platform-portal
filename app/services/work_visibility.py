"""Who may see a task or a delegation rule: its owner, an admin, or everyone when it is public.

Both tables carry ``visibility``: ``private`` (the default, the owner and
admins only) or ``public`` (every signed-in member). It is the same two-state
rule assistants use (``agents.visibility``), so the lists, the detail routes
and the overview service all ask this module instead of each spelling the
rule out. A task made by a delegation rule takes the rule's visibility; a
follow-up or a rerun keeps its task's.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_

VISIBILITY_PRIVATE = "private"
VISIBILITY_PUBLIC = "public"
VISIBILITIES = (VISIBILITY_PRIVATE, VISIBILITY_PUBLIC)


def normalize_visibility(value: Any, *, default: str = VISIBILITY_PRIVATE) -> str:
    """``private`` or ``public``; empty means ``default``; anything else is a ValueError."""
    cleaned = str(value or "").strip().lower()
    if not cleaned:
        return default
    if cleaned not in VISIBILITIES:
        raise ValueError("visibility must be 'private' or 'public'")
    return cleaned


def is_admin(user: Any) -> bool:
    return str(getattr(user, "role", "") or "").strip().lower() == "admin"


def can_see(row: Any, user: Any) -> bool:
    """Whether ``user`` may read ``row`` (anything with ``owner_user_id`` and ``visibility``)."""
    if user is None or row is None:
        return False
    if is_admin(user):
        return True
    if getattr(row, "owner_user_id", None) == getattr(user, "id", None):
        return True
    return str(getattr(row, "visibility", "") or VISIBILITY_PRIVATE).strip().lower() == VISIBILITY_PUBLIC


def visibility_clause(model: Any, user: Any):
    """A SQLAlchemy filter selecting the rows ``user`` may read; None for an admin (no filter)."""
    if user is None:
        return model.visibility == VISIBILITY_PUBLIC
    if is_admin(user):
        return None
    return or_(model.owner_user_id == getattr(user, "id", None), model.visibility == VISIBILITY_PUBLIC)
