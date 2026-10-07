"""Pull the administrator's Default connectors into an existing member's settings.

A member's settings row is copied from the admin seed once, when it is
created (``RuntimeProfileService.get_or_create_for_user``); the seed never
reaches a row that already exists. This module is how a member catches up
afterwards, one connector at a time: ``preview_connector_defaults`` compares
the seed's sections of a connector with the member's and says what would
change, ``apply_connector_defaults`` builds the new config from the member's
decisions. Both are pure functions over two config dicts; the web route owns
the row, the Secret and the restarts.

The vocabulary of a preview:

- addition: something the seed has and the member does not -- a new instance,
  a field the member left blank. Applied without asking.
- conflict: both sides set and different. The member decides per item:
  ``admin`` (take the seed's value), ``mine`` (leave the row alone) or, for an
  instance row, ``both`` (keep theirs and add the seed's beside it).

Only what the member's own panel shows is compared (``MEMBER_FIELD_TREE``):
the seed may carry more through the admin API, but a value the member could
never see or undo is not pulled in. Both configs are read in their sanitized
shape (``sanitize_runtime_profile_config_dict``), so nothing the save would
drop is promised. Credentials are compared but never displayed: a preview says
"Set" or "Not set" for them, and every other displayed value goes through the
log redactor in case a credential was typed into a URL or a name.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.redaction import redact_text
from app.schemas.runtime_profile import (
    TROUBLESHOOTING_INSTANCE_SECTIONS,
    sanitize_runtime_profile_config_dict,
)
from app.services.connector_registry import ConnectorSpec
from app.services.profile_secret_encryption import SENSITIVE_FIELD_NAMES

CHOICE_ADMIN = "admin"
CHOICE_MINE = "mine"
CHOICE_BOTH = "both"
CHOICES = (CHOICE_ADMIN, CHOICE_MINE, CHOICE_BOTH)

SECRET_SET = "Set"
SECRET_UNSET = "Not set"

# Appended to the seed's copy of an instance when the member keeps both and
# the names collide. Every instance list gets the rename: the CLIs address a
# row as --instance <name>, the nexus/splunk/pgsql sanitizers keep one row per
# name, and the forms refuse duplicates.
BOTH_SUFFIX = "administrator default"
_BOTH_SUFFIX_RE = re.compile(r"\s*\(" + re.escape(BOTH_SUFFIX) + r"(?: \d+)?\)$", re.IGNORECASE)

# Sections a connector may own that are not a shared shape: the model
# provider is a personal authorization, the commit identity is the member's
# name, and debug is not a setting at all.
ADMIN_DEFAULTS_SKIPPED_SECTIONS = frozenset({"llm", "git", "debug"})

# Longest value a preview shows; a certificate or a long URL is cut short.
DISPLAY_MAX_CHARS = 120

_INSTANCE_FIELDS = {"enabled", "name", "url", "username", "password", "token"}

# What the member's panel renders per section (the forms under
# partials/connectors/); a nested dict is a sub-object, a set the fields of
# the rows in a list. Mirrors the field lists _settings_merge_payload reads.
MEMBER_FIELD_TREE: dict[str, dict] = {
    "jira": {"enabled": True, "instances": _INSTANCE_FIELDS | {"project", "api_version"}},
    "confluence": {"enabled": True, "instances": _INSTANCE_FIELDS | {"space"}},
    "github": {"enabled": True, "base_url": True, "api_token": True},
    "jenkins": {"enabled": True, "instances": set(_INSTANCE_FIELDS)},
    "nexus": {"enabled": True, "default_instance": True, "instances": set(_INSTANCE_FIELDS)},
    "splunk": {
        "enabled": True,
        "default_instance": True,
        "instances": _INSTANCE_FIELDS | {"default_index", "default_earliest", "max_results", "app", "owner"},
    },
    "pgsql": {
        "enabled": True,
        "default_instance": True,
        "instances": {
            "enabled", "name", "host", "port", "database", "username", "password", "sslmode",
            "statement_timeout_seconds", "max_rows",
        },
    },
    "aws": {
        "enabled": True,
        "domain": True,
        "username": True,
        "password": True,
        "provider": True,
        "idp_url": True,
        "source_profile": True,
        "default_account": True,
        "default_region": True,
        "kubeconfig_path": True,
        "session_duration_seconds": True,
        "accounts": {"enabled", "name", "account_id", "role", "regions"},
        "eks_clusters": {"enabled", "account", "cluster", "region", "private_endpoint", "server_ca", "tls_server_name"},
    },
    "mobile-auto": {
        "enabled": True,
        "browserstack": {"username": True, "access_key": True, "api_base_url": True, "appium_base_url": True},
        "defaults": {
            "platform": True,
            "network_mode": True,
            "idle_timeout_seconds": True,
            "appium_version": True,
            "video": True,
            "interactive_debugging": True,
        },
    },
    "proxy": {"enabled": True, "url": True, "username": True, "password": True},
}

# A field the panel shows with a concrete value when nothing is stored. A
# seed value that differs from it is a clash, not a blank to fill: the
# member's sign-in or connection would change under them.
DISPLAY_DEFAULTS: dict[tuple[str, ...], Any] = {
    ("aws", "provider"): "adfs-assume",
    ("aws", "eks_clusters", "server_ca"): "cluster",
    ("pgsql", "instances", "sslmode"): "require",
    ("pgsql", "instances", "port"): 5432,
    ("pgsql", "instances", "statement_timeout_seconds"): 30,
    ("pgsql", "instances", "max_rows"): 5000,
}

SECTION_LABELS = {
    "jira": "Jira",
    "confluence": "Confluence",
    "github": "GitHub",
    "git": "Commit identity",
    "jenkins": "Jenkins",
    "nexus": "Nexus",
    "splunk": "Splunk",
    "pgsql": "PostgreSQL",
    "aws": "AWS",
    "mobile-auto": "BrowserStack",
    "proxy": "Proxy",
    "llm": "Model provider",
}

LIST_ITEM_LABELS = {
    ("aws", "accounts"): "AWS account",
    ("aws", "eks_clusters"): "EKS cluster",
}

FIELD_LABELS = {
    "url": "URL",
    "base_url": "API base URL",
    "api_token": "API token",
    "token": "API token",
    "access_key": "Access key",
    "api_key": "API key",
    "username": "Username",
    "password": "Password",
    "project": "Project",
    "space": "Space key",
    "api_version": "API version",
    "name": "Name",
    "host": "Host",
    "port": "Port",
    "database": "Database",
    "sslmode": "SSL mode",
    "statement_timeout_seconds": "Statement timeout (seconds)",
    "max_rows": "Max rows",
    "default_index": "Default index",
    "default_earliest": "Default earliest",
    "max_results": "Max results",
    "app": "App",
    "owner": "Owner",
    "default_instance": "Default instance",
    "account_id": "Account id",
    "role": "IAM role",
    "regions": "Regions",
    "account": "Account",
    "cluster": "Cluster",
    "region": "Region",
    "private_endpoint": "Private endpoint",
    "server_ca": "Server CA",
    "tls_server_name": "TLS server name",
    "domain": "Domain",
    "provider": "Provider",
    "idp_url": "ADFS IdP URL",
    "source_profile": "Source profile",
    "default_account": "Default account",
    "default_region": "Default region",
    "session_duration_seconds": "Session duration (seconds)",
    "kubeconfig_path": "Kubeconfig path",
    "email": "Email",
    # Nested holders that add nothing to a label: "BrowserStack access key",
    # not "BrowserStack BrowserStack access key".
    "user": "",
    "browserstack": "",
    "defaults": "Default",
    "platform": "Platform",
    "network_mode": "Network",
    "idle_timeout_seconds": "Idle timeout (seconds)",
    "appium_version": "Appium version",
    "api_base_url": "API URL",
    "appium_base_url": "Appium hub URL",
    "video": "Video",
    "interactive_debugging": "Interactive debugging",
}

# Fields compared as identities: case and a trailing slash do not count.
_IDENTITY_FIELDS = frozenset(
    {
        "url",
        "host",
        "name",
        "account",
        "cluster",
        "account_id",
        "base_url",
        "api_base_url",
        "appium_base_url",
        "idp_url",
        "private_endpoint",
        "default_account",
        "default_instance",
        "email",
    }
)


class DefaultsChanged(Exception):
    """The defaults, or the member's row, changed between the preview and the apply."""


# --------------------------------------------------------------------------- ops


@dataclass
class _SetOp:
    """Write a scalar (or list of scalars) at ``path``."""

    path: tuple[str, ...]
    value: Any
    only_if_missing: bool = False
    order: int = 0

    def apply(self, config: dict) -> None:
        parent = _ensure_dict(config, self.path[:-1])
        key = self.path[-1]
        if self.only_if_missing and key in parent:
            return
        parent[key] = copy.deepcopy(self.value)


@dataclass
class _MergeItemOp:
    """Lay the seed's row over the member's row ``index`` of the list at ``path``.

    The seed's keys win; keys only the member has stay, except that a seed
    row carrying a credential replaces the member's credentials as a group
    (a token and a password do not add up to one sign-in). A new name is
    followed by everything in the section that referred to the old one.
    """

    path: tuple[str, ...]
    index: int
    item: dict
    seed_accounts: list = field(default_factory=list)
    order: int = 0

    def apply(self, config: dict) -> None:
        rows = _ensure_list(config, self.path)
        if self.index >= len(rows) or not isinstance(rows[self.index], dict):
            return
        mine = rows[self.index]
        merged = dict(mine)
        if self.path == ("aws", "eks_clusters") and "account" in self.item:
            # The seed names the account its way; the row keeps naming it the
            # member's way (the two were matched through the account id).
            account = _member_account_name(config, self.item.get("account"), self.seed_accounts)
            merged["account"] = account or mine.get("account")
            self.item = {key: value for key, value in self.item.items() if key != "account"}
        if any(_is_secret(key) and not _blank(value) for key, value in self.item.items()):
            for key in list(merged):
                if _is_secret(key) and key not in self.item:
                    merged.pop(key)
        for key, value in self.item.items():
            if key == "enabled":
                # Whether a row is on is the member's choice, like the section switch.
                continue
            merged[key] = copy.deepcopy(value)
        old_name = str(mine.get("name") or "")
        new_name = str(merged.get("name") or "")
        if _name_taken(rows, new_name, skip=self.index):
            # The seed calls the same server by a name another of the member's
            # rows already has; two rows sharing one would lose one to the
            # sanitizers, so the member's name stays (the preview says so).
            merged["name"] = old_name
            new_name = old_name
        rows[self.index] = merged
        if new_name and _norm(new_name) != _norm(old_name):
            _rename_references(config, self.path, old_name, new_name)


@dataclass
class _AppendItemOp:
    """Add the seed's row to the list at ``path``, renamed if its name is taken."""

    path: tuple[str, ...]
    item: dict
    seed_accounts: list = field(default_factory=list)
    order: int = 1

    def apply(self, config: dict) -> None:
        item = copy.deepcopy(self.item)
        if self.path == ("aws", "eks_clusters"):
            # The seed names the account its way; the row has to name it the
            # member's way, or the AWS panel refuses the member's next save.
            # Resolved before the list is touched, so a row that cannot be
            # placed leaves the config exactly as it was.
            account = _member_account_name(config, item.get("account"), self.seed_accounts)
            if not account:
                return
            item["account"] = account
        rows = _ensure_list(config, self.path)
        name = str(item.get("name") or "").strip()
        if name and _name_taken(rows, name):
            candidate = f"{name} ({BOTH_SUFFIX})"
            counter = 2
            while _name_taken(rows, candidate):
                candidate = f"{name} ({BOTH_SUFFIX} {counter})"
                counter += 1
            item["name"] = candidate
        rows.append(item)


@dataclass
class _Addition:
    id: str
    label: str
    detail: str
    ops: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "detail": self.detail}


@dataclass
class _Conflict:
    id: str
    kind: str  # "item" | "field"
    label: str
    fields: list[dict]
    options: tuple[str, ...]
    ops_by_choice: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "fields": list(self.fields),
            "options": list(self.options),
        }

    def ops_for(self, choice: str) -> list:
        return list(self.ops_by_choice.get(choice) or [])


@dataclass
class _Plan:
    available: bool
    fingerprint: str
    additions: list[_Addition]
    conflicts: list[_Conflict]

    def preview(self, spec: ConnectorSpec) -> dict:
        return {
            "connector": {"type": spec.type, "label": spec.label},
            "available": self.available,
            "up_to_date": self.available and not self.additions and not self.conflicts,
            "fingerprint": self.fingerprint,
            "additions": [item.as_dict() for item in self.additions],
            "conflicts": [item.as_dict() for item in self.conflicts],
        }


# ----------------------------------------------------------------------- helpers


def _norm(value: Any) -> str:
    return str(value or "").strip().rstrip("/").lower()


def _base_name(value: Any) -> str:
    """A name without the suffix "Keep both" adds, so the copy still matches its seed row."""

    return _BOTH_SUFFIX_RE.sub("", str(value or "").strip())


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _same(key: str, mine: Any, theirs: Any) -> bool:
    if isinstance(mine, list) or isinstance(theirs, list):
        mine_items = mine if isinstance(mine, list) else [mine]
        theirs_items = theirs if isinstance(theirs, list) else [theirs]
        return sorted(_norm(item) for item in mine_items) == sorted(_norm(item) for item in theirs_items)
    if key == "name":
        return _norm(_base_name(mine)) == _norm(_base_name(theirs))
    if key in _IDENTITY_FIELDS:
        return _norm(mine) == _norm(theirs)
    if isinstance(mine, bool) or isinstance(theirs, bool):
        return mine == theirs
    if isinstance(mine, (int, float)) and isinstance(theirs, (int, float)):
        return mine == theirs
    return str(mine or "").strip() == str(theirs or "").strip()


def _is_secret(key: str) -> bool:
    return key in SENSITIVE_FIELD_NAMES


def _display(key: str, value: Any) -> str:
    if _is_secret(key):
        return SECRET_UNSET if _blank(value) else SECRET_SET
    if _blank(value):
        return ""
    if isinstance(value, bool):
        return "On" if value else "Off"
    if isinstance(value, list):
        text = ", ".join(str(item) for item in value)
    else:
        text = str(value)
    text = redact_text(text.strip())
    if len(text) > DISPLAY_MAX_CHARS:
        text = text[: DISPLAY_MAX_CHARS - 1] + "…"
    return text


def _field_label(path: tuple[str, ...]) -> str:
    """Label of a field by its path below the section (list indexes excluded)."""

    parts = [FIELD_LABELS[part] if part in FIELD_LABELS else part.replace("_", " ").capitalize() for part in path]
    parts = [part for part in parts if part]
    if not parts:
        return ""
    label = parts[0]
    for part in parts[1:]:
        label = _join_label(label, part)
    return label


def _acronym_start(label: str) -> bool:
    return len(label) > 1 and label[0].isupper() and label[1].isupper()


def _join_label(head: str, tail: str) -> str:
    """"GitHub" + "API base URL" -> "GitHub API base URL"; "Proxy" + "Username" -> "Proxy username"."""

    if not tail:
        return head
    if _acronym_start(tail):
        return f"{head} {tail}"
    return f"{head} {tail[:1].lower()}{tail[1:]}"


def _item_kind_label(list_path: tuple[str, ...]) -> str:
    if list_path in LIST_ITEM_LABELS:
        return LIST_ITEM_LABELS[list_path]
    section = SECTION_LABELS.get(list_path[0], list_path[0])
    return f"{section} instance"


def _item_name(item: Mapping[str, Any]) -> str:
    for key in ("name", "url", "host", "cluster", "account_id"):
        value = str(item.get(key) or "").strip()
        if value:
            return redact_text(value)
    return "(unnamed)"


def _identity_tiers(list_path: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    """How rows of a list are told apart, most specific key first."""

    if list_path == ("pgsql", "instances"):
        return (("host", "database"), ("name",))
    if list_path == ("aws", "accounts"):
        return (("account_id",), ("name",))
    if list_path == ("aws", "eks_clusters"):
        # The region is a field of the row, not part of what names it: a row
        # without one and the seed's row with one are the same cluster.
        return (("account", "cluster"),)
    if list_path[-1] == "instances":
        return (("url",), ("name",))
    return (("name",), ("url",))


def _offers_both(list_path: tuple[str, ...]) -> bool:
    # An AWS account is one account id, a private endpoint one cluster: a second
    # row for either is not "both", it is a duplicate the sanitizers resolve.
    return list_path[-1] == "instances"


def _identity_value(item: Mapping[str, Any], keys: tuple[str, ...], accounts: Mapping[str, str]) -> tuple[str, ...] | None:
    values = []
    for key in keys:
        raw = item.get(key)
        if key == "name":
            value = _norm(_base_name(raw))
        elif key == "account":
            value = accounts.get(_norm(raw), _norm(raw))
        else:
            value = _norm(raw)
        if not value:
            return None
        values.append(value)
    return tuple(values)


def _account_ids(aws: Mapping[str, Any] | None) -> dict[str, str]:
    """``{lowercased account name or id: account id}`` of an aws section's account rows."""

    result: dict[str, str] = {}
    rows = aws.get("accounts") if isinstance(aws, Mapping) and isinstance(aws.get("accounts"), list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        account_id = str(row.get("account_id") or "").strip()
        name = _norm(row.get("name"))
        if account_id:
            result[account_id] = account_id
            if name:
                result[name] = account_id
    return result


def _member_account_name(config: Mapping[str, Any], account: Any, seed_accounts: list) -> str:
    """How the member's row names the account a seed EKS row refers to ("" when they lack it)."""

    aws = config.get("aws") if isinstance(config.get("aws"), dict) else {}
    rows = aws.get("accounts") if isinstance(aws.get("accounts"), list) else []
    wanted = _norm(account)
    if not wanted:
        return ""
    for row in rows:
        if isinstance(row, dict) and (_norm(row.get("name")) == wanted or str(row.get("account_id") or "").strip() == wanted):
            return str(row.get("name") or row.get("account_id") or "")
    # The seed may say "cps-dev" where the member calls that account id "dev".
    seed_ids = _account_ids({"accounts": seed_accounts})
    account_id = seed_ids.get(wanted, "")
    if account_id:
        for row in rows:
            if isinstance(row, dict) and str(row.get("account_id") or "").strip() == account_id:
                return str(row.get("name") or account_id)
    return ""


def _name_taken(rows: list, name: Any, skip: int | None = None) -> bool:
    wanted = _norm(name)
    if not wanted:
        return False
    for index, row in enumerate(rows):
        if index == skip or not isinstance(row, dict):
            continue
        if _norm(row.get("name")) == wanted:
            return True
    return False


def _rename_references(config: dict, list_path: tuple[str, ...], old: str, new: str) -> None:
    """Follow a renamed row: the section's default and, for AWS, the EKS rows."""

    section = config.get(list_path[0]) if isinstance(config.get(list_path[0]), dict) else None
    if section is None:
        return
    if list_path == ("aws", "accounts"):
        if _norm(section.get("default_account")) == _norm(old):
            section["default_account"] = new
        for row in section.get("eks_clusters") if isinstance(section.get("eks_clusters"), list) else []:
            if isinstance(row, dict) and _norm(row.get("account")) == _norm(old):
                row["account"] = new
    elif list_path[-1] == "instances" and _norm(section.get("default_instance")) == _norm(old):
        section["default_instance"] = new


def _ensure_dict(config: dict, path: tuple[str, ...]) -> dict:
    node = config
    for key in path:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    return node


def _ensure_list(config: dict, path: tuple[str, ...]) -> list:
    parent = _ensure_dict(config, path[:-1])
    rows = parent.get(path[-1])
    if not isinstance(rows, list):
        rows = []
        parent[path[-1]] = rows
    return rows


def _prune(value: Any, tree: Any) -> Any:
    """``value`` reduced to what ``tree`` (MEMBER_FIELD_TREE) knows."""

    if tree is True:
        return value
    if isinstance(tree, dict):
        if not isinstance(value, dict):
            return {}
        return {key: _prune(child, tree[key]) for key, child in value.items() if key in tree}
    if isinstance(tree, (set, frozenset)):
        if not isinstance(value, list):
            return []
        return [{key: child for key, child in row.items() if key in tree} for row in value if isinstance(row, dict)]
    return value


def _masked(value: Any) -> Any:
    """``value`` with every credential replaced by whether it is set, for the fingerprint."""

    if isinstance(value, dict):
        return {
            key: ("set" if not _blank(child) else "unset") if _is_secret(key) else _masked(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_masked(item) for item in value]
    return value


def _fingerprint(seed_slice: Mapping[str, Any], member_slice: Mapping[str, Any]) -> str:
    """What the member looked at: the seed's and their own sections, credentials as set/unset.

    The same for every member with the same row, so a staleness check, not a
    token. A seed or row that changes between preview and apply changes it.
    """

    canonical = json.dumps(
        {"seed": _masked(seed_slice), "mine": _masked(member_slice)},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# -------------------------------------------------------------------- the walk


class _Walker:
    def __init__(self, seed_slice: Mapping[str, Any], member: Mapping[str, Any]) -> None:
        self.additions: list[_Addition] = []
        self.conflicts: list[_Conflict] = []
        self._used_ids: set[str] = set()
        self._their_accounts = _account_ids(seed_slice.get("aws"))
        self._mine_accounts = _account_ids(member.get("aws"))
        aws = seed_slice.get("aws") if isinstance(seed_slice.get("aws"), dict) else {}
        self._seed_accounts = [row for row in (aws.get("accounts") or []) if isinstance(row, dict)]

    # A dict of the seed laid over the member's dict at the same path.
    def dict(self, path: tuple[str, ...], theirs: Mapping[str, Any], mine: Any, hidden: list | None = None) -> None:
        mine_present = isinstance(mine, dict)
        mine_dict: Mapping[str, Any] = mine if mine_present else {}
        # Ops every change below carries along: a section the member never
        # had starts the way a new member's would, switch included; a section
        # they have keeps its switch.
        hidden = list(hidden or [])
        if not mine_present and "enabled" in theirs:
            hidden.append(_SetOp(path + ("enabled",), bool(theirs["enabled"]), only_if_missing=True))
        for key, value in theirs.items():
            if key == "enabled" or _blank(value):
                continue
            child_path = path + (key,)
            if isinstance(value, dict):
                self.dict(child_path, value, mine_dict.get(key), hidden)
                continue
            if isinstance(value, list) and all(isinstance(item, dict) for item in value):
                self.items(child_path, value, mine_dict.get(key), hidden)
                continue
            self.scalar(child_path, key, value, mine_dict.get(key), hidden)

    # One field: a string, a number, a bool, or a list of strings.
    def scalar(self, path: tuple[str, ...], key: str, theirs: Any, mine: Any, hidden: list) -> None:
        label = _join_label(SECTION_LABELS.get(path[0], path[0]), _field_label(path[1:]))
        field_id = ".".join(path)
        if _blank(mine):
            mine = DISPLAY_DEFAULTS.get(path)
        if _blank(mine):
            self.additions.append(
                _Addition(id=field_id, label=label, detail=_display(key, theirs), ops=list(hidden) + [_SetOp(path, theirs)])
            )
            return
        if _same(key, mine, theirs):
            return
        self.conflicts.append(
            _Conflict(
                id=field_id,
                kind="field",
                label=label,
                fields=[
                    {
                        "label": _field_label(path[1:]),
                        "mine": _display(key, mine),
                        "theirs": _display(key, theirs),
                        "secret": _is_secret(key),
                    }
                ],
                options=(CHOICE_ADMIN, CHOICE_MINE),
                ops_by_choice={CHOICE_ADMIN: list(hidden) + [_SetOp(path, theirs)]},
            )
        )

    # A list of instance-like rows, matched by identity.
    def items(self, path: tuple[str, ...], theirs: list[dict], mine: Any, hidden: list) -> None:
        # Indexes into the member's list as stored, which is what the ops address.
        mine_rows = [(index, row) for index, row in enumerate(mine if isinstance(mine, list) else []) if isinstance(row, dict)]
        taken: set[int] = set()
        kind_label = _item_kind_label(path)
        options = (CHOICE_ADMIN, CHOICE_MINE, CHOICE_BOTH) if _offers_both(path) else (CHOICE_ADMIN, CHOICE_MINE)
        append_order = 2 if path == ("aws", "eks_clusters") else 1
        for their_item in theirs:
            match = self._match(path, their_item, mine_rows, taken)
            if match is None:
                if path == ("aws", "eks_clusters") and not self._eks_account_known(their_item):
                    # Names an account neither side has: it could never be
                    # placed in the member's row, so it is not promised.
                    continue
                self.additions.append(
                    _Addition(
                        id=self._unique_id(f"{'.'.join(path)}:{self._identity_id(path, their_item)}"),
                        label=f"{kind_label} {_item_name(their_item)}",
                        detail="New: " + _item_summary(their_item),
                        ops=list(hidden) + [_AppendItemOp(path, dict(their_item), self._seed_accounts, order=append_order)],
                    )
                )
                continue
            index, mine_item, identity, fills, clashes = match
            taken.add(index)
            if clashes and _name_taken([row for _, row in mine_rows], their_item.get("name"), skip=index):
                # The seed's name is another of the member's rows: taking it
                # would make two rows share one, so it is not offered.
                clashes = [clash for clash in clashes if clash["key"] != "name"]
            # The member's row index keeps the id unique when two seed rows
            # share one URL (one Jira site, two projects).
            conflict_id = self._unique_id(f"{'.'.join(path)}:{identity}#{index}")
            label = f"{kind_label} {_item_name(mine_item)}"
            if clashes:
                self.conflicts.append(
                    _Conflict(
                        id=conflict_id,
                        kind="item",
                        label=label,
                        fields=[{key: value for key, value in clash.items() if key != "key"} for clash in clashes],
                        options=options,
                        ops_by_choice={
                            CHOICE_ADMIN: list(hidden) + [_MergeItemOp(path, index, dict(their_item), self._seed_accounts)],
                            CHOICE_BOTH: list(hidden) + [_AppendItemOp(path, dict(their_item), self._seed_accounts, order=append_order)],
                        },
                    )
                )
            elif fills:
                self.additions.append(
                    _Addition(
                        id=conflict_id,
                        label=label,
                        detail="Fills in: " + " · ".join(f"{_field_label((key,))} {_display(key, value)}" for key, value in fills),
                        ops=list(hidden) + [_MergeItemOp(path, index, {key: value for key, value in fills}, self._seed_accounts)],
                    )
                )

    def _compare_items(self, path: tuple[str, ...], their_item: Mapping[str, Any], mine_item: Mapping[str, Any]):
        """``(fills, clashes)`` of a seed row laid over a member row."""

        fills: list[tuple[str, Any]] = []
        clashes: list[dict] = []
        mine_has_secret = any(_is_secret(key) and not _blank(value) for key, value in mine_item.items())
        for key, value in their_item.items():
            if key == "enabled" or _blank(value):
                continue
            mine_value = mine_item.get(key)
            if (
                key == "account"
                and path == ("aws", "eks_clusters")
                and not _blank(mine_value)
                # Compared through the account id: "dev" and "cps-dev" may be one account.
                and self._mine_accounts.get(_norm(mine_value), _norm(mine_value))
                == self._their_accounts.get(_norm(value), _norm(value))
            ):
                continue
            if _blank(mine_value):
                mine_value = DISPLAY_DEFAULTS.get(path + (key,))
            if _blank(mine_value):
                if _is_secret(key) and mine_has_secret:
                    # Another kind of credential is set: this is a change of
                    # sign-in, not a blank to fill.
                    clashes.append({"key": key, "label": _field_label((key,)), "mine": SECRET_UNSET, "theirs": SECRET_SET, "secret": True})
                else:
                    fills.append((key, value))
            elif not _same(key, mine_value, value):
                clashes.append(
                    {
                        "key": key,
                        "label": _field_label((key,)),
                        "mine": _display(key, mine_value),
                        "theirs": _display(key, value),
                        "secret": _is_secret(key),
                    }
                )
        return fills, clashes

    def _match(self, path, their_item: Mapping[str, Any], mine_rows: list[tuple[int, dict]], taken: set[int]):
        """The member row the seed row is: most identity tiers in common, then fewest clashes."""

        tiers = _identity_tiers(path)
        their_ids = [_identity_value(their_item, keys, self._their_accounts) for keys in tiers]
        best = None
        for index, mine_item in mine_rows:
            if index in taken:
                continue
            score = 0
            identity = ""
            for tier, (keys, their_id) in enumerate(zip(tiers, their_ids)):
                if their_id is None:
                    continue
                if _identity_value(mine_item, keys, self._mine_accounts) == their_id:
                    # The first tier is the most specific (a URL over a name),
                    # so a row matching it outranks any number of later ones.
                    score += 1 << (len(tiers) - tier)
                    identity = identity or _identity_text(keys, their_id)
            if not score:
                continue
            fills, clashes = self._compare_items(path, their_item, mine_item)
            rank = (-score, len(clashes), index)
            if best is None or rank < best[0]:
                best = (rank, index, mine_item, identity, fills, clashes)
        if best is None:
            return None
        return best[1:]

    def _identity_id(self, path: tuple[str, ...], item: Mapping[str, Any]) -> str:
        for keys in _identity_tiers(path):
            value = _identity_value(item, keys, self._their_accounts)
            if value is not None:
                return _identity_text(keys, value)
        return "item"

    def _unique_id(self, candidate: str) -> str:
        unique = candidate
        counter = 2
        while unique in self._used_ids:
            unique = f"{candidate}~{counter}"
            counter += 1
        self._used_ids.add(unique)
        return unique

    def _eks_account_known(self, item: Mapping[str, Any]) -> bool:
        wanted = _norm(item.get("account"))
        return bool(wanted) and (wanted in self._their_accounts or wanted in self._mine_accounts)


def _identity_text(keys: tuple[str, ...], values: tuple[str, ...]) -> str:
    """An id component from identity values; a credential typed into a URL stays out of it."""

    return "=".join(["+".join(keys), "|".join(redact_text(value) for value in values)])


def _item_summary(item: Mapping[str, Any]) -> str:
    parts = []
    for key, value in item.items():
        if key == "enabled" or _blank(value):
            continue
        parts.append(f"{_field_label((key,))} {_display(key, value)}")
    return " · ".join(parts) if parts else "(no details)"


# ------------------------------------------------------------------ public API


def admin_defaults_sections(spec: ConnectorSpec) -> tuple[str, ...]:
    """The sections of ``spec`` the admin defaults cover."""

    return tuple(
        section
        for section in spec.config_sections
        if section not in ADMIN_DEFAULTS_SKIPPED_SECTIONS and section in MEMBER_FIELD_TREE
    )


def _slice(config: Mapping[str, Any] | None, sections: tuple[str, ...]) -> dict:
    sanitized = sanitize_runtime_profile_config_dict(dict(config or {}))
    result = {}
    for section in sections:
        pruned = _prune(sanitized.get(section), MEMBER_FIELD_TREE[section])
        if isinstance(pruned, dict) and pruned:
            result[section] = pruned
    return result


def seed_slice_for(spec: ConnectorSpec, seed: Mapping[str, Any] | None) -> dict:
    """The seed's sections this connector owns, sanitized and reduced to what the member's panel shows."""

    return _slice(seed, admin_defaults_sections(spec))


def _plan(spec: ConnectorSpec, member_config: Mapping[str, Any] | None, seed: Mapping[str, Any] | None) -> _Plan:
    sections = admin_defaults_sections(spec)
    seed_slice = _slice(seed, sections)
    member = sanitize_runtime_profile_config_dict(dict(member_config or {}))
    member_slice = _slice(member, sections)
    walker = _Walker(seed_slice, member)
    for section, theirs in seed_slice.items():
        walker.dict((section,), theirs, member.get(section))
    return _Plan(
        available=bool(seed_slice),
        fingerprint=_fingerprint(seed_slice, member_slice),
        additions=walker.additions,
        conflicts=walker.conflicts,
    )


def preview_connector_defaults(
    spec: ConnectorSpec, member_config: Mapping[str, Any] | None, seed: Mapping[str, Any] | None
) -> dict:
    """What pulling the admin defaults into ``member_config`` would change.

    Returns ``{connector, available, up_to_date, fingerprint, additions,
    conflicts}``; see the module docstring. Credential values never appear.
    """

    return _plan(spec, member_config, seed).preview(spec)


def apply_connector_defaults(
    spec: ConnectorSpec,
    member_config: Mapping[str, Any] | None,
    seed: Mapping[str, Any] | None,
    decisions: Mapping[str, str] | None,
    fingerprint: str | None,
) -> tuple[dict, dict]:
    """Build the member's new config from their decisions.

    ``decisions`` maps a conflict id from the preview to ``admin``, ``mine``
    or ``both``; a conflict without a decision keeps the member's value. Ids
    the preview did not list are ignored. Raises ``DefaultsChanged`` when
    ``fingerprint`` is not the preview's (the seed or the member's row changed
    in the meantime, so the member looks again rather than applying blind) and
    ``ValueError`` for a choice that is not one of the three or that the
    conflict does not offer.

    Returns ``(new_config, summary)``; the summary counts what was done, never
    values, for the audit row. ``summary["changes"]`` is 0 when nothing was
    written, so the caller can skip the save.
    """

    plan = _plan(spec, member_config, seed)
    if not plan.available:
        raise DefaultsChanged("Your administrator has not set Default connectors for this connector.")
    if (fingerprint or "").strip() != plan.fingerprint:
        raise DefaultsChanged(
            "The Default connectors or your settings changed while you were looking at them. Check them again."
        )
    conflicts_by_id = {conflict.id: conflict for conflict in plan.conflicts}
    chosen: dict[str, str] = {}
    for conflict_id, choice in dict(decisions or {}).items():
        clean = str(choice or "").strip().lower()
        conflict = conflicts_by_id.get(str(conflict_id))
        if clean not in CHOICES or (conflict is not None and clean not in conflict.options):
            raise ValueError("Some choices were not recognized. Check the Default connectors again.")
        if conflict is not None:
            chosen[conflict.id] = clean

    # The ops address the member's rows by index as the plan saw them, which
    # is the sanitized shape; the route sanitizes the result again anyway.
    config = copy.deepcopy(sanitize_runtime_profile_config_dict(dict(member_config or {})))
    ops: list = []
    counts = {CHOICE_ADMIN: 0, CHOICE_MINE: 0, CHOICE_BOTH: 0}
    for addition in plan.additions:
        ops.extend(addition.ops)
    for conflict in plan.conflicts:
        choice = chosen.get(conflict.id, CHOICE_MINE)
        counts[choice] += 1
        ops.extend(conflict.ops_for(choice))
    # Merges address the member's rows by index, so they run before anything
    # is appended; EKS rows go last so the accounts they name are there.
    for op in sorted(ops, key=lambda item: item.order):
        op.apply(config)
    if ops:
        _drop_dangling_defaults(config)
    summary = {
        "connector": spec.type,
        "sections": list(admin_defaults_sections(spec)),
        "additions": len(plan.additions),
        "conflicts": len(plan.conflicts),
        "choices": counts,
        "changes": len(ops),
    }
    return config, summary


def _drop_dangling_defaults(config: dict) -> None:
    """A default instance/account the seed names but the member does not have is dropped.

    Renames are followed (``_rename_references``) and EKS rows are named the
    member's way on append, so this is the last resort: the forms refuse such
    a default on save, and it would otherwise be stored and then refused the
    next time the member saves anything.
    """

    for section in TROUBLESHOOTING_INSTANCE_SECTIONS:
        block = config.get(section)
        if not isinstance(block, dict) or not block.get("default_instance"):
            continue
        wanted = _norm(block.get("default_instance"))
        rows = block.get("instances") if isinstance(block.get("instances"), list) else []
        if not any(isinstance(row, dict) and _norm(row.get("name")) == wanted for row in rows):
            block.pop("default_instance", None)
    aws = config.get("aws")
    if isinstance(aws, dict) and aws.get("default_account"):
        wanted = str(aws.get("default_account") or "").strip()
        rows = aws.get("accounts") if isinstance(aws.get("accounts"), list) else []
        if not any(
            isinstance(row, dict)
            and (_norm(row.get("name")) == wanted.lower() or str(row.get("account_id") or "").strip() == wanted)
            for row in rows
        ):
            aws.pop("default_account", None)


__all__ = [
    "ADMIN_DEFAULTS_SKIPPED_SECTIONS",
    "BOTH_SUFFIX",
    "CHOICES",
    "CHOICE_ADMIN",
    "CHOICE_BOTH",
    "CHOICE_MINE",
    "DISPLAY_DEFAULTS",
    "MEMBER_FIELD_TREE",
    "DefaultsChanged",
    "admin_defaults_sections",
    "apply_connector_defaults",
    "preview_connector_defaults",
    "seed_slice_for",
]
