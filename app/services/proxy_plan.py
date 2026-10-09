"""The Proxy connector resolved the way the runtimes resolve it.

A member's ``proxy`` section is a list of named proxies, the default one, and
an assignment per connector (MULTI_PROXY_CONNECTOR_PLAN.md)::

    proxy:
      enabled: true
      default: corp-a
      proxies:
        - {name: corp-a, url: http://proxy-a.example.test:3128, username: u, password: p, no_proxy: localhost}
        - {name: corp-b, url: https://proxy-b.example.test}
      assignments: {llm: corp-b, pgsql: none, jira: ""}

In the pod the default proxy is the environment (HTTPS_PROXY), and every
other connector's proxy is written into its tool's own config. The Portal
needs the same answer in two places: the connection Test buttons, which
probe along the path the runtime's CLI takes, and the Secret projection,
which mirrors the default entry as the flat ``url``/``username``/``password``
keys so a runtime image built before named proxies keeps its proxy. This
module is the Portal's copy of the runtimes' ``proxy_plan`` resolution (the
native runtime's ``src/utils/proxy_plan.py``); keep the rules in step.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlparse

from app.schemas.runtime_profile import PROXY_LEGACY_ENTRY_NAME, sanitize_runtime_profile_proxy

# The native runtime's default NO_PROXY (src/utils/proxy.py DEFAULT_NO_PROXY),
# applied when a proxy names none: in-cluster names, loopback and the
# metadata endpoint never go through the proxy.
DEFAULT_NO_PROXY = "localhost,127.0.0.1,169.254.169.254,.svc.cluster.local"

# What a choice is.
KIND_ENVIRONMENT = "environment"
KIND_NONE = "none"
KIND_PROXY = "proxy"

# Why a choice came out the way it did.
SOURCE_DISABLED = "disabled"
SOURCE_ENVIRONMENT = "environment"
SOURCE_ASSIGNMENT = "assignment"
SOURCE_INSTANCE = "instance"
SOURCE_DEFAULT = "default"
SOURCE_NO_PROXY = "no_proxy"
SOURCE_UNKNOWN = "unknown"

_NONE_WORDS = frozenset({"none", "direct", "off"})
_ENVIRONMENT_WORDS = frozenset({"", "env", "environment"})
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def proxy_url_with_credentials(url: str, username: Any, password: Any) -> str:
    """The connector's credentials go into the URL the way the runtime exports
    them (src/utils/proxy.py), so one parser serves both."""
    if not (username and password):
        return url
    parsed = urlparse(url if "://" in url else "http://" + url)
    if parsed.username is not None or not parsed.netloc:
        return url
    hostport = parsed.netloc.rsplit("@", 1)[-1]
    netloc = f"{quote(str(username), safe='')}:{quote(str(password), safe='')}@{hostport}"
    return parsed._replace(netloc=netloc).geturl()


@dataclass(frozen=True)
class ProxyEntry:
    name: str
    url: str
    username: str = ""
    password: str = ""
    no_proxy: str = ""

    def url_with_credentials(self) -> str:
        return proxy_url_with_credentials(self.url, self.username or None, self.password or None)

    def no_proxy_text(self, default: str = DEFAULT_NO_PROXY) -> str:
        return self.no_proxy.strip() or default

    def address(self) -> str:
        """host:port without credentials, for a message."""
        parsed = urlparse(self.url if "://" in self.url else "http://" + self.url)
        host = parsed.hostname or ""
        if ":" in host:
            host = "[" + host + "]"
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        return f"{host}:{port}" if host else ""


@dataclass(frozen=True)
class ProxyChoice:
    kind: str
    source: str
    entry: ProxyEntry | None = None
    url: str = ""

    @property
    def setting(self) -> str:
        """"", "none", or the proxy URL with credentials: the runtime's vocabulary."""
        if self.kind == KIND_NONE:
            return "none"
        if self.kind == KIND_PROXY:
            return self.entry.url_with_credentials() if self.entry is not None else self.url
        return ""

    @property
    def proxy_url(self) -> str | None:
        """The proxy a probe goes through, or None for a direct connection."""
        return self.setting or None if self.kind == KIND_PROXY else None


@dataclass(frozen=True)
class ProxyPlan:
    enabled: bool
    entries: tuple[ProxyEntry, ...] = ()
    default: ProxyEntry | None = None
    assignments: Mapping[str, str] = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        return self.enabled and self.default is not None

    def entry(self, name: str) -> ProxyEntry | None:
        wanted = _text(name)
        for item in self.entries:
            if item.name == wanted:
                return item
        return None

    def choice(self, connector: str, *, host: str | None = None, instance_setting: str | None = None) -> ProxyChoice:
        """The proxy for ``connector``; see the runtime's proxy_plan for the rules."""
        # The row's own setting is explicit and stands on its own: none and a
        # URL apply whatever the connector's switch says; a name needs the
        # connector's list, so it only resolves while the connector is on.
        own = _text(instance_setting)
        if own:
            if own.lower() in _NONE_WORDS:
                return ProxyChoice(KIND_NONE, SOURCE_INSTANCE)
            entry = self.entry(own) if self.enabled else None
            if entry is not None:
                return self._entry_choice(entry, host, SOURCE_INSTANCE)
            if _looks_like_url(own):
                return ProxyChoice(KIND_PROXY, SOURCE_INSTANCE, url=own if "://" in own else "http://" + own)
        if not self.enabled:
            return ProxyChoice(KIND_ENVIRONMENT, SOURCE_DISABLED)
        assigned = _text(self.assignments.get(connector))
        if assigned.lower() in _ENVIRONMENT_WORDS:
            return ProxyChoice(KIND_ENVIRONMENT, SOURCE_ENVIRONMENT)
        if assigned.lower() in _NONE_WORDS:
            return ProxyChoice(KIND_NONE, SOURCE_ASSIGNMENT)
        entry = self.entry(assigned)
        if entry is None:
            return ProxyChoice(KIND_ENVIRONMENT, SOURCE_UNKNOWN)
        return self._entry_choice(entry, host, SOURCE_ASSIGNMENT)

    def _entry_choice(self, entry: ProxyEntry, host: str | None, source: str) -> ProxyChoice:
        if host and no_proxy_exempts(host, entry.no_proxy_text()):
            return ProxyChoice(KIND_NONE, SOURCE_NO_PROXY, entry=entry)
        if source == SOURCE_ASSIGNMENT and self.default is not None and entry.name == self.default.name:
            return ProxyChoice(KIND_ENVIRONMENT, SOURCE_DEFAULT, entry=entry)
        return ProxyChoice(KIND_PROXY, source, entry=entry)

    def egress(self, connector: str, *, host: str, instance_setting: str | None = None) -> ProxyChoice:
        """Where the runtime's connection to ``host`` actually leaves: the
        choice, with "the environment" resolved to the default proxy (or a
        direct connection when its NO_PROXY exempts the host, or when there is
        no default). What a Portal probe should imitate."""
        choice = self.choice(connector, host=host, instance_setting=instance_setting)
        if choice.kind != KIND_ENVIRONMENT:
            return choice
        if not self.configured:
            return ProxyChoice(KIND_NONE, choice.source)
        if no_proxy_exempts(host, self.default.no_proxy_text()):
            return ProxyChoice(KIND_NONE, SOURCE_NO_PROXY, entry=self.default)
        return ProxyChoice(KIND_PROXY, SOURCE_DEFAULT, entry=self.default)


def _looks_like_url(value: str) -> bool:
    text = _text(value)
    if "://" in text:
        return True
    host, sep, port = text.rpartition(":")
    return bool(sep) and host != "" and port.isdigit()


def build_proxy_plan(raw: Any) -> ProxyPlan:
    """The plan for a proxy section in any shape (the sanitizer upgrades the legacy one)."""
    section = sanitize_runtime_profile_proxy(raw) if isinstance(raw, Mapping) else {}
    entries = tuple(
        ProxyEntry(
            name=_text(item.get("name")),
            url=_text(item.get("url")),
            username=_text(item.get("username")),
            password="" if item.get("password") is None else str(item.get("password")),
            no_proxy=_text(item.get("no_proxy")),
        )
        for item in (section.get("proxies") or [])
        if isinstance(item, Mapping) and _text(item.get("url"))
    )
    default = None
    wanted = _text(section.get("default"))
    for item in entries:
        if item.name == wanted:
            default = item
            break
    if default is None and entries:
        default = entries[0]
    assignments = {str(key): _text(value) for key, value in (section.get("assignments") or {}).items()}
    return ProxyPlan(enabled=bool(section.get("enabled")), entries=entries, default=default, assignments=assignments)


def mirror_default_proxy(section: Any) -> Any:
    """The section with its default entry copied to the flat legacy keys.

    Written into the Secret projection only: a runtime image from before named
    proxies reads ``url``/``username``/``password``/``no_proxy`` and keeps its
    proxy; a current one prefers ``proxies``. The stored row never carries it.
    """
    if not isinstance(section, Mapping):
        return section
    out = dict(section)
    plan = build_proxy_plan(out)
    if plan.default is None:
        return out
    out["url"] = plan.default.url
    for key, value in (("username", plan.default.username), ("password", plan.default.password), ("no_proxy", plan.default.no_proxy)):
        if value:
            out[key] = value
        else:
            out.pop(key, None)
    return out


def no_proxy_exempts(host: str, no_proxy: str) -> bool:
    """Whether a NO_PROXY list keeps ``host`` off the proxy, the way the Go CLIs decide it."""
    target = _text(host).lower().rstrip(".")
    if target.startswith("[") and target.endswith("]"):
        target = target[1:-1]
    if not target:
        return False
    if target in _LOOPBACK_HOSTS:
        return True
    for raw_entry in re.split(r"[,\s]+", no_proxy or ""):
        entry = raw_entry.strip().lower()
        if not entry:
            continue
        if entry == "*":
            return True
        for prefix in ("http://", "https://"):
            if entry.startswith(prefix):
                entry = entry[len(prefix):]
        if entry.startswith("[") and "]" in entry:
            entry = entry[1 : entry.index("]")]
        elif entry.count(":") == 1:
            entry = entry.split(":", 1)[0]
        if entry.startswith("*."):
            entry = entry[1:]
        if entry.startswith("."):
            suffix = entry[1:]
            if target == suffix or target.endswith(entry):
                return True
            continue
        if target == entry or target.endswith("." + entry):
            return True
    return False


def hostname_of(url: str) -> str:
    """The host of a URL (or of a bare host[:port]) for a NO_PROXY check."""
    text = _text(url)
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else "//" + text)
    return (parsed.hostname or "").strip().lower()


__all__ = [
    "DEFAULT_NO_PROXY",
    "KIND_ENVIRONMENT",
    "KIND_NONE",
    "KIND_PROXY",
    "PROXY_LEGACY_ENTRY_NAME",
    "ProxyChoice",
    "ProxyEntry",
    "ProxyPlan",
    "build_proxy_plan",
    "hostname_of",
    "mirror_default_proxy",
    "no_proxy_exempts",
    "proxy_url_with_credentials",
]
