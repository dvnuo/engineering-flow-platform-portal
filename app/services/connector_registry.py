"""Registry of connector types Portal knows how to configure.

A connector is a per-member capability that lives outside the assistant pod:
either a program on the member's own PC (``kind="local"``, reached through the
chat page) or an external service (``kind="remote"``). Adding a type is one
``ConnectorSpec`` here plus a panel template and, for local types, a page
module under ``static/js/connectors/``. The transport between page, Portal and
runtime is connector-type agnostic (docs/CONNECTORS_CONTRACT.md).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any


LOCAL_BROWSER_TYPE = "local_browser"
LOCAL_BROWSER_DEFAULT_PORT = 8765


@dataclass(frozen=True)
class ConnectorSpec:
    type: str
    label: str
    kind: str  # "local" | "remote"
    category: str
    description: str
    panel_template: str
    guidance_key: str
    config_defaults: dict[str, Any] = field(default_factory=dict)
    validate_config: Callable[[Mapping[str, Any] | None], dict[str, Any]] = lambda config: {}

    def normalized_config(self, config: Mapping[str, Any] | None) -> dict[str, Any]:
        """Validate ``config`` and fill in defaults; raises ValueError."""

        return self.validate_config(config)


def _validate_local_browser_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(config or {})
    allowed = {"auto_enable_in_new_chats", "preferred_port"}
    unknown = sorted(str(key) for key in source.keys() if key not in allowed)
    if unknown:
        raise ValueError(f"Unknown local_browser config keys: {', '.join(unknown)}")

    auto_enable = source.get("auto_enable_in_new_chats", True)
    if isinstance(auto_enable, str):
        auto_enable = auto_enable.strip().lower() in {"1", "true", "yes", "on"}
    elif not isinstance(auto_enable, bool):
        raise ValueError("auto_enable_in_new_chats must be a boolean")

    port = source.get("preferred_port", LOCAL_BROWSER_DEFAULT_PORT)
    if isinstance(port, bool):
        raise ValueError("preferred_port must be an integer between 1024 and 65535")
    if isinstance(port, str) and port.strip():
        try:
            port = int(port.strip())
        except ValueError as exc:
            raise ValueError("preferred_port must be an integer between 1024 and 65535") from exc
    if port is None or port == "":
        port = LOCAL_BROWSER_DEFAULT_PORT
    if not isinstance(port, int) or port < 1024 or port > 65535:
        raise ValueError("preferred_port must be an integer between 1024 and 65535")

    return {"auto_enable_in_new_chats": bool(auto_enable), "preferred_port": int(port)}


CONNECTOR_REGISTRY: dict[str, ConnectorSpec] = {
    LOCAL_BROWSER_TYPE: ConnectorSpec(
        type=LOCAL_BROWSER_TYPE,
        label="Local browser",
        kind="local",
        category="Local devices & tools",
        description=(
            "Let assistants read and operate pages in a Chrome window on your own PC, "
            "using your existing logins. Runs through the EFP browser bridge; nothing "
            "leaves your machine except what you ask the assistant to look at."
        ),
        panel_template="partials/connector_local_browser_panel.html",
        guidance_key=LOCAL_BROWSER_TYPE,
        config_defaults={"auto_enable_in_new_chats": True, "preferred_port": LOCAL_BROWSER_DEFAULT_PORT},
        validate_config=_validate_local_browser_config,
    ),
}


def get_connector_spec(connector_type: str) -> ConnectorSpec:
    """Return the spec for ``connector_type``; raises KeyError when unknown."""

    return CONNECTOR_REGISTRY[str(connector_type or "").strip()]


def list_connector_specs() -> list[ConnectorSpec]:
    return list(CONNECTOR_REGISTRY.values())


def is_known_connector(connector_type: str) -> bool:
    return str(connector_type or "").strip() in CONNECTOR_REGISTRY


__all__ = [
    "CONNECTOR_REGISTRY",
    "ConnectorSpec",
    "LOCAL_BROWSER_DEFAULT_PORT",
    "LOCAL_BROWSER_TYPE",
    "get_connector_spec",
    "is_known_connector",
    "list_connector_specs",
]
