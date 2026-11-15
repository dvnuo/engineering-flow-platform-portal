"""``efp-app.json``: the app's manifest (docs/CONTRACTS.md, section 2) and who may register it.

The Portal validates the manifest before it sends anything to the host, so a
member reads what is wrong when they register or deploy rather than in the
middle of an installation. The host validates it again for itself. Unknown
keys are ignored; paths may not be absolute or contain ``..``.

``owners`` is what lets a member register a directory: the platform's
service account may read far more repositories than the member, so the
manifest has to name them. An app created from a published template skips
that check (publishing the template is the author's consent).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any

from app.i18n import N_
from app.services.apps.errors import ManifestError, NotListedAsOwner
from app.services.apps.names import APP_TYPES

MANIFEST_FILENAME = "efp-app.json"
MANIFEST_MAX_BYTES = 64 * 1024

NAME_MAX_LENGTH = 80
DESCRIPTION_MAX_LENGTH = 500
DEFAULT_REQUIREMENTS = "requirements.txt"
DEFAULT_HEALTH_PATH = "/"
DEFAULT_STARTUP_TIMEOUT = 60
MAX_STARTUP_TIMEOUT = 300
PATH_MAX_LENGTH = 512

# module:attribute, each a dotted Python name.
_MODULE_ATTRIBUTE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")

Problem = tuple[str, dict]


@dataclass(frozen=True)
class Manifest:
    name: str
    type: str
    description: str = ""
    icon: str | None = None
    entry: str | None = None
    command: str | None = None
    requirements: str = DEFAULT_REQUIREMENTS
    health_path: str = DEFAULT_HEALTH_PATH
    startup_timeout: int = DEFAULT_STARTUP_TIMEOUT
    strip_prefix: bool | None = None
    owners: tuple[str, ...] = ()
    raw: dict = field(default_factory=dict, compare=False, repr=False)


def is_safe_relative_path(value: Any) -> bool:
    """A relative path inside the entry directory: not absolute, no ``..`` segment, no control character."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or len(text) > PATH_MAX_LENGTH:
        return False
    if text.startswith(("/", "\\")) or _DRIVE_RE.match(text):
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        return False
    return ".." not in re.split(r"[\\/]+", text)


def _is_safe_health_path(value: Any) -> bool:
    """A URL path of the app itself: no scheme or host, no ``..`` segment."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or len(text) > PATH_MAX_LENGTH:
        return False
    if "://" in text or text.startswith("//") or "\\" in text:
        return False
    if any(ord(char) <= 32 or ord(char) == 127 for char in text):
        return False
    return ".." not in text.split("?", 1)[0].split("/")


def _optional_path(data: dict, key: str, default: str | None, problems: list[Problem]) -> str | None:
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if not is_safe_relative_path(value):
        problems.append((N_("\"{field}\" must be a relative path inside the entry directory, without \"..\"."), {"field": key}))
        return default
    return value.strip()


def validate_manifest(data: Any) -> Manifest:
    """The manifest of a parsed JSON value; a ManifestError naming every problem otherwise."""
    if not isinstance(data, dict):
        raise ManifestError([(N_("The file must hold one JSON object."), {})])
    problems: list[Problem] = []

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append((N_("\"name\" is required."), {}))
        name = ""
    else:
        name = name.strip()
        if len(name) > NAME_MAX_LENGTH:
            problems.append((N_("\"name\" may be at most {limit} characters."), {"limit": NAME_MAX_LENGTH}))

    description = data.get("description")
    if description is None:
        description = ""
    elif not isinstance(description, str):
        problems.append((N_("\"description\" must be text."), {}))
        description = ""
    else:
        description = description.strip()
        if len(description) > DESCRIPTION_MAX_LENGTH:
            problems.append((N_("\"description\" may be at most {limit} characters."), {"limit": DESCRIPTION_MAX_LENGTH}))

    app_type = data.get("type")
    if not isinstance(app_type, str) or app_type not in APP_TYPES:
        problems.append((N_("\"type\" must be one of: {types}."), {"types": ", ".join(APP_TYPES)}))
        app_type = ""

    icon = _optional_path(data, "icon", None, problems)
    requirements = _optional_path(data, "requirements", DEFAULT_REQUIREMENTS, problems) or DEFAULT_REQUIREMENTS

    entry = data.get("entry")
    command = data.get("command")
    if app_type in ("fastapi", "flask"):
        if not isinstance(entry, str) or not _MODULE_ATTRIBUTE_RE.match(entry.strip()):
            problems.append((N_("\"entry\" must be module:attribute for a {type} app, for example main:app."), {"type": app_type}))
            entry = None
        else:
            entry = entry.strip()
    elif app_type in ("streamlit", "gradio"):
        if not is_safe_relative_path(entry):
            problems.append((N_("\"entry\" must be the path of the script to run for a {type} app, for example app.py."), {"type": app_type}))
            entry = None
        else:
            entry = entry.strip()
    elif app_type == "static":
        if entry is None:
            entry = "."
        elif not is_safe_relative_path(entry):
            problems.append((N_("\"entry\" must be a directory inside the entry directory for a static app."), {}))
            entry = None
        else:
            entry = entry.strip()
    else:
        # Ignored for a command app.
        entry = entry.strip() if isinstance(entry, str) and entry.strip() else None

    if app_type == "command":
        if not isinstance(command, str) or not command.strip():
            problems.append((N_("\"command\" is required for a command app."), {}))
            command = None
        else:
            command = command.strip()
    else:
        command = command.strip() if isinstance(command, str) and command.strip() else None

    health_path = data.get("health_path")
    if health_path is None:
        health_path = DEFAULT_HEALTH_PATH
    elif not _is_safe_health_path(health_path):
        problems.append((N_("\"health_path\" must be a path of the app, for example /health."), {}))
        health_path = DEFAULT_HEALTH_PATH
    else:
        health_path = health_path.strip()

    startup_timeout = data.get("startup_timeout")
    if startup_timeout is None:
        startup_timeout = DEFAULT_STARTUP_TIMEOUT
    elif isinstance(startup_timeout, bool) or not isinstance(startup_timeout, int) or not 1 <= startup_timeout <= MAX_STARTUP_TIMEOUT:
        problems.append((N_("\"startup_timeout\" must be a whole number of seconds from 1 to {limit}."), {"limit": MAX_STARTUP_TIMEOUT}))
        startup_timeout = DEFAULT_STARTUP_TIMEOUT

    strip_prefix = data.get("strip_prefix")
    if strip_prefix is not None and not isinstance(strip_prefix, bool):
        problems.append((N_("\"strip_prefix\" must be true, false or null."), {}))
        strip_prefix = None

    owners_value = data.get("owners")
    owners: tuple[str, ...] = ()
    if owners_value is None:
        owners = ()
    elif not isinstance(owners_value, list) or not all(isinstance(item, str) for item in owners_value):
        problems.append((N_("\"owners\" must be a list of Portal usernames."), {}))
    else:
        owners = tuple(item.strip() for item in owners_value if item.strip())

    if problems:
        raise ManifestError(problems)
    return Manifest(
        name=name,
        type=app_type,
        description=description,
        icon=icon,
        entry=entry,
        command=command,
        requirements=requirements,
        health_path=health_path,
        startup_timeout=startup_timeout,
        strip_prefix=strip_prefix,
        owners=owners,
        raw=dict(data),
    )


def parse_manifest(content: bytes | str) -> Manifest:
    """The manifest of the file's content; a ManifestError when it is not valid."""
    if isinstance(content, bytes):
        if len(content) > MANIFEST_MAX_BYTES:
            raise ManifestError([(N_("The file is larger than {limit} KB."), {"limit": MANIFEST_MAX_BYTES // 1024})])
        try:
            # utf-8-sig: an editor's byte-order mark is not a syntax error.
            content = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ManifestError([(N_("The file is not UTF-8 text."), {})]) from None
    try:
        data = json.loads(content)
    except ValueError as exc:
        position = f"line {exc.lineno}, column {exc.colno}" if isinstance(exc, json.JSONDecodeError) else ""
        raise ManifestError([(N_("The file is not valid JSON ({position})."), {"position": position or "unknown position"})]) from None
    return validate_manifest(data)


def is_listed_owner(manifest: Manifest, username: str) -> bool:
    wanted = str(username or "").strip().lower()
    return bool(wanted) and wanted in {owner.lower() for owner in manifest.owners}


def check_ownership(manifest: Manifest, username: str, *, from_template: bool = False) -> None:
    """Refuse a member the manifest does not name under ``owners``.

    ``username`` is the member's Portal username, never the identity behind
    a connector. ``from_template`` skips the check for an app created from a
    published template.
    """
    if from_template:
        return
    if not is_listed_owner(manifest, username):
        raise NotListedAsOwner(username=str(username or ""))
