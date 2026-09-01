"""Icons offered when an admin creates or edits an assistant type.

A free-text field asking for a Lucide icon name only works for someone who has
the Lucide catalogue memorized. This is the curated set the picker shows, chosen
to cover the kinds of work an assistant type usually represents, so choosing an
icon is recognition rather than recall.

Every name here is verified against the bundled `lucide.min.js`. Adding one
without checking produces a silently blank icon, so keep the list and the bundle
in step.
"""
from __future__ import annotations

DEFAULT_ASSISTANT_TYPE_ICON = "bot"

# Grouped by the kind of work they suggest; the picker renders them in order.
ASSISTANT_TYPE_ICONS: tuple[str, ...] = (
    # General
    "bot",
    "sparkles",
    "compass",
    "lightbulb",
    # Requirements and documentation
    "clipboard-list",
    "file-text",
    "book-open",
    "pen-tool",
    "briefcase",
    "users",
    # Quality
    "clipboard-check",
    "flask-conical",
    "bug",
    "microscope",
    "target",
    "shield",
    # Engineering
    "code",
    "terminal",
    "git-branch",
    "git-pull-request",
    "layers",
    "hammer",
    "wrench",
    # Operations
    "server-cog",
    "cloud",
    "database",
    "gauge",
    "rocket",
    # Analysis and communication
    "chart-line",
    "search",
    "message-square",
    "palette",
)


def is_known_icon(value: str | None) -> bool:
    return bool(value) and value in ASSISTANT_TYPE_ICONS
