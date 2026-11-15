"""``efp-app.json``: the app's manifest (docs/CONTRACTS.md, section 2) and who may register it.

The Portal validates the manifest before it sends anything to the host, so a
member reads what is wrong when they register or deploy rather than in the
middle of an installation. The host validates it again for itself, and the
two must agree on every manifest: what passes here passes there and the
reverse. The host's ``efp_app_host/manifest.py`` (in the app repository) is
the reference, and the rules below follow it value by value. In particular
nothing is trimmed before it is checked: a space around an entry or a path is
part of the value, as it is for the host. Unknown keys are ignored; paths may
not be absolute or contain ``..``.

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
HEALTH_PATH_MAX_LENGTH = 200
COMMAND_MAX_LENGTH = 2000

# module:attribute, each a dotted Python name.
_MODULE_ATTRIBUTE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
# A URL path of the app itself: a leading slash, then URL characters only (no "#", no space).
_HEALTH_PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/?-]*$")
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
    """A relative path the Portal itself reads from a repository: not absolute, no ``..`` segment, no control character.

    The Portal's own, stricter rule for an app's entry directory and for the
    files it fetches (app/services/apps/source.py). The paths inside a
    manifest are judged by ``is_manifest_path``, which is the host's rule.
    """
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


def is_manifest_path(value: Any) -> bool:
    """A path of the manifest that stays inside the entry directory, by the host's rule.

    Text as it is written (nothing trimmed), not empty, forward slashes only,
    not absolute, no drive letter, no ``..`` segment, no NUL.
    """
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        return False
    if value.startswith("/") or _DRIVE_RE.match(value):
        return False
    return all(part != ".." for part in value.split("/"))


def normalize_manifest_path(value: str) -> str:
    """The path without ``.`` segments and doubled slashes; ``""`` stands for the entry directory itself."""
    return "/".join(part for part in value.split("/") if part not in ("", "."))


def _relative_path_problem(key: str) -> Problem:
    return (N_("\"{field}\" must be a relative path inside the entry directory, without \"..\"."), {"field": key})


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
    elif len(description) > DESCRIPTION_MAX_LENGTH:
        # Counted as written, the spaces around it included, as the host counts it.
        problems.append((N_("\"description\" may be at most {limit} characters."), {"limit": DESCRIPTION_MAX_LENGTH}))
        description = ""
    else:
        # Trimmed only for showing it.
        description = description.strip()

    app_type = data.get("type")
    if not isinstance(app_type, str) or app_type not in APP_TYPES:
        problems.append((N_("\"type\" must be one of: {types}."), {"types": ", ".join(APP_TYPES)}))
        app_type = ""

    icon = data.get("icon")
    if icon is None:
        pass
    elif not is_manifest_path(icon):
        # An empty string is not "no icon": leave the key out for that.
        problems.append(_relative_path_problem("icon"))
        icon = None
    else:
        icon = normalize_manifest_path(icon) or None

    requirements = data.get("requirements")
    if requirements is None or requirements == "":
        requirements = DEFAULT_REQUIREMENTS
    if not is_manifest_path(requirements) or not normalize_manifest_path(requirements):
        problems.append(_relative_path_problem("requirements"))
        requirements = DEFAULT_REQUIREMENTS
    else:
        requirements = normalize_manifest_path(requirements)

    entry = data.get("entry")
    command = data.get("command")
    if app_type in ("fastapi", "flask"):
        if not isinstance(entry, str) or not _MODULE_ATTRIBUTE_RE.match(entry):
            problems.append((N_("\"entry\" must be module:attribute for a {type} app, for example main:app."), {"type": app_type}))
            entry = None
        command = None
    elif app_type in ("streamlit", "gradio"):
        if not is_manifest_path(entry) or not normalize_manifest_path(entry):
            problems.append((N_("\"entry\" must be the path of the script to run for a {type} app, for example app.py."), {"type": app_type}))
            entry = None
        else:
            entry = normalize_manifest_path(entry)
        command = None
    elif app_type == "static":
        if entry is None or entry == "":
            entry = "."
        if not is_manifest_path(entry):
            problems.append((N_("\"entry\" must be a directory inside the entry directory for a static app."), {}))
            entry = None
        else:
            entry = normalize_manifest_path(entry) or "."
        command = None
    elif app_type == "command":
        # Ignored for a command app.
        entry = None
        if not isinstance(command, str) or not command.strip():
            problems.append((N_("\"command\" is required for a command app."), {}))
            command = None
        elif len(command) > COMMAND_MAX_LENGTH or any(char in command for char in "\x00\r\n"):
            problems.append((N_("\"command\" must be a single line of at most {limit} characters."), {"limit": COMMAND_MAX_LENGTH}))
            command = None
    else:
        # No valid type: nothing more can be said about the entry or the command.
        entry = None
        command = None

    health_path = data.get("health_path")
    if health_path is None or health_path == "":
        health_path = DEFAULT_HEALTH_PATH
    if not isinstance(health_path, str) or len(health_path) > HEALTH_PATH_MAX_LENGTH or not _HEALTH_PATH_RE.match(health_path):
        problems.append((N_("\"health_path\" must be a path of the app, for example /health."), {}))
        health_path = DEFAULT_HEALTH_PATH

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
    elif not isinstance(owners_value, list) or not all(isinstance(item, str) and item for item in owners_value):
        # An empty string is not a username.
        problems.append((N_("\"owners\" must be a list of Portal usernames."), {}))
    else:
        # Compared without the spaces around a name (is_listed_owner).
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
