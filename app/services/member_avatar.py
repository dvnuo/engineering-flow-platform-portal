"""The member's avatar: the company photo directory, or their initials.

The photo directory serves one square picture per member at
``<host>/casual/square/<first four characters of the username>/<username>.jpg``.
Only the host differs between deployments, so that is what AVATAR_PHOTO_HOST
configures; the path is the directory's own convention. An empty host keeps
the photo off and the account button shows initials. Not every member has a
picture, so the browser falls back to the same initials when the request
fails (chat_ui.js marks the avatar ``is-fallback`` on the image's error event).
"""
from __future__ import annotations

import re
from urllib.parse import quote

PHOTO_PATH_TEMPLATE = "/casual/square/{prefix}/{username}.jpg"
PHOTO_PREFIX_LENGTH = 4
_NAME_SEPARATORS = re.compile(r"[\s._\-]+")


def avatar_photo_url(username: str | None, host: str | None) -> str:
    """Absolute photo URL for ``username``, or "" when the host is not set."""
    base = (host or "").strip().rstrip("/")
    name = (username or "").strip()
    if not base or not name:
        return ""
    if "://" not in base:
        base = f"https://{base}"
    prefix = name[:PHOTO_PREFIX_LENGTH]
    return base + PHOTO_PATH_TEMPLATE.format(
        prefix=quote(prefix, safe=""),
        username=quote(name, safe=""),
    )


def avatar_initials(display_name: str | None, username: str | None = None) -> str:
    """Up to two characters that stand in for the photo.

    Two or more words give the first letter of the first and the last word
    ("Jane Doe" -> "JD"); a single word gives its first two characters, which
    also suits a short CJK name. The username is the last resort.
    """
    text = (display_name or "").strip() or (username or "").strip()
    if not text:
        return "?"
    words = [word for word in _NAME_SEPARATORS.split(text) if word]
    if len(words) >= 2:
        return (words[0][0] + words[-1][0]).upper()
    return (words[0] if words else text)[:2].upper()
