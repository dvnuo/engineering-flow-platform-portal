"""Registry of connector types Portal knows how to configure.

Connectors are the one place a member configures what their assistants can
reach. There are two kinds:

- ``settings``: a service the assistant signs in to from its pod (the model
  provider, Jira, GitHub, AWS, ...). Its values live in the member's settings
  row (``runtime_profiles``, one per member) under ``config_sections``, are
  rendered into the pod Secret, and reach a running assistant when it
  restarts. The panel is a form partial under ``partials/connectors/``;
  ``form_sections`` are the ``__touch_<section>`` flags that form posts, which
  is the vocabulary ``web._settings_merge_payload`` reads.
- ``local``: a program on the member's own PC, reached through the chat page
  (docs/CONNECTORS_CONTRACT.md). Its settings live in ``user_connectors`` and
  never enter a pod; for local types add a page module under
  ``static/js/connectors/``.

Adding a type is one ``ConnectorSpec`` here plus its panel template.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any


KIND_LOCAL = "local"
KIND_SETTINGS = "settings"

# Display order of the categories in the Connectors list.
CATEGORY_ORDER = (
    "Model",
    "Work tracking & docs",
    "Code & delivery",
    "Cloud & data",
    "Testing",
    "Network",
    "Local devices & tools",
)

# What a connector's state reads as in the list and panel header.
STATE_CONNECTED = "connected"
STATE_OFF = "off"
STATE_NOT_SET_UP = "not_set_up"

LOCAL_BROWSER_TYPE = "local_browser"
LOCAL_BROWSER_DEFAULT_PORT = 8765

# Download packages the Portal offers, one zip per entry built by
# scripts/browser-bridge/package.sh in the tools repository (the binary, the
# installer for that system, and a README; nothing else). The member's own
# system is offered first, the rest under "Other systems".
LOCAL_BROWSER_PLATFORMS: tuple[tuple[str, str], ...] = (
    ("windows-amd64", "Windows (x64)"),
    ("windows-arm64", "Windows (ARM64)"),
    ("darwin-arm64", "macOS (Apple silicon)"),
    ("darwin-amd64", "macOS (Intel)"),
    ("linux-amd64", "Linux (x64)"),
    ("linux-arm64", "Linux (ARM64)"),
)


def local_browser_platform_label(platform: str) -> str:
    for key, label in LOCAL_BROWSER_PLATFORMS:
        if key == platform:
            return label
    return platform


def detect_local_browser_platform(user_agent: str | None) -> str:
    """Best guess of the member's package from the User-Agent header.

    Chrome on Apple silicon still reports "Intel Mac OS X", so a Mac gets the
    Apple silicon package here and the page corrects it with client hints
    (navigator.userAgentData architecture); the other systems only need the OS.
    """

    ua = str(user_agent or "").lower()
    arm = any(token in ua for token in ("arm64", "aarch64", "armv8"))
    if "windows" in ua:
        return "windows-arm64" if arm else "windows-amd64"
    if "mac os x" in ua or "macintosh" in ua:
        return "darwin-arm64"
    if "linux" in ua or "x11" in ua:
        return "linux-arm64" if arm else "linux-amd64"
    return LOCAL_BROWSER_PLATFORMS[0][0]


@dataclass(frozen=True)
class ConnectorSpec:
    type: str
    label: str
    kind: str  # KIND_LOCAL | KIND_SETTINGS
    category: str
    description: str
    panel_template: str
    guidance_key: str
    icon: str = "plug"
    config_defaults: dict[str, Any] = field(default_factory=dict)
    validate_config: Callable[[Mapping[str, Any] | None], dict[str, Any]] = lambda config: {}
    # Deployment-level values the page needs for this type (read-only for the
    # member, unlike ``config``); resolved per request so settings stay live.
    settings_provider: Callable[[], dict[str, Any]] = lambda: {}
    # KIND_SETTINGS only: the settings keys this connector owns, the form
    # sections its panel posts, the connection tests it offers, extra guidance
    # entries shown under the main one (GitHub carries the Git identity), and
    # how to read its state from the member's settings.
    config_sections: tuple[str, ...] = ()
    form_sections: tuple[str, ...] = ()
    test_targets: tuple[str, ...] = ()
    extra_guidance_keys: tuple[str, ...] = ()
    state_of: Callable[[Mapping[str, Any]], str] = lambda config: STATE_NOT_SET_UP

    @property
    def is_settings(self) -> bool:
        return self.kind == KIND_SETTINGS

    def normalized_config(self, config: Mapping[str, Any] | None) -> dict[str, Any]:
        """Validate ``config`` and fill in defaults; raises ValueError."""

        return self.validate_config(config)

    def server_settings(self) -> dict[str, Any]:
        return dict(self.settings_provider() or {})


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


def _local_browser_settings() -> dict[str, Any]:
    from app.config import get_settings

    # Raw value on purpose: the page resolves a path against the origin it
    # runs on, which is also the origin it hands the bridge.
    return {"start_url": str(get_settings().local_browser_start_url or "").strip()}


def _section(config: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key) if isinstance(config, Mapping) else None
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _enabled_or_off(section: Mapping[str, Any], configured: bool) -> str:
    if not configured:
        return STATE_NOT_SET_UP
    return STATE_CONNECTED if section.get("enabled") else STATE_OFF


def _llm_state(config: Mapping[str, Any]) -> str:
    llm = _section(config, "llm")
    if _text(llm.get("provider")) == "ai_platform":
        auth = _section(_section(llm, "ai_platform"), "auth")
        return STATE_CONNECTED if _text(auth.get("password")) else STATE_NOT_SET_UP
    return STATE_CONNECTED if _text(llm.get("api_key")) else STATE_NOT_SET_UP


def _proxy_state(config: Mapping[str, Any]) -> str:
    proxy = _section(config, "proxy")
    return _enabled_or_off(proxy, bool(_text(proxy.get("url"))))


def _instances_state(key: str, address_fields: tuple[str, ...] = ("url",)) -> Callable[[Mapping[str, Any]], str]:
    def state(config: Mapping[str, Any]) -> str:
        section = _section(config, key)
        instances = section.get("instances")
        rows = [item for item in instances if isinstance(item, dict)] if isinstance(instances, list) else []
        if not rows and _text(section.get("url")):
            # Jenkins sections saved before multi-instance support are flat.
            rows = [section]
        usable = [
            row
            for row in rows
            if row.get("enabled", True) is not False and any(_text(row.get(name)) for name in address_fields)
        ]
        if usable:
            return _enabled_or_off(section, True)
        return STATE_OFF if rows else STATE_NOT_SET_UP

    return state


def _github_state(config: Mapping[str, Any]) -> str:
    github = _section(config, "github")
    token = github.get("api_token") or github.get("token") or github.get("access_token")
    return _enabled_or_off(github, bool(_text(token)))


def _aws_state(config: Mapping[str, Any]) -> str:
    aws = _section(config, "aws")
    return _enabled_or_off(aws, bool(_text(aws.get("username")) or aws.get("accounts")))


def _browserstack_state(config: Mapping[str, Any]) -> str:
    mobile = _section(config, "mobile-auto")
    return _enabled_or_off(mobile, bool(_text(_section(mobile, "browserstack").get("username"))))


def _settings_connector(
    type: str,
    label: str,
    category: str,
    description: str,
    *,
    icon: str,
    state_of: Callable[[Mapping[str, Any]], str],
    config_sections: tuple[str, ...] | None = None,
    form_sections: tuple[str, ...] | None = None,
    test_targets: tuple[str, ...] = (),
    guidance_key: str | None = None,
    extra_guidance_keys: tuple[str, ...] = (),
) -> ConnectorSpec:
    return ConnectorSpec(
        type=type,
        label=label,
        kind=KIND_SETTINGS,
        category=category,
        description=description,
        panel_template=f"partials/connectors/{type}.html",
        guidance_key=guidance_key or type,
        icon=icon,
        config_sections=config_sections or (type,),
        form_sections=form_sections or (type,),
        test_targets=test_targets,
        extra_guidance_keys=extra_guidance_keys,
        state_of=state_of,
    )


SETTINGS_CONNECTORS: tuple[ConnectorSpec, ...] = (
    _settings_connector(
        "llm",
        "Model provider",
        "Model",
        "The model your assistants think with: GitHub Copilot or AI Platform, and the defaults they start a chat with.",
        icon="sparkles",
        state_of=_llm_state,
        test_targets=("llm",),
    ),
    _settings_connector(
        "jira",
        "Jira",
        "Work tracking & docs",
        "Read and update issues on one or more Jira sites.",
        icon="square-kanban",
        state_of=_instances_state("jira"),
        test_targets=("jira",),
    ),
    _settings_connector(
        "confluence",
        "Confluence",
        "Work tracking & docs",
        "Search and read pages on one or more Confluence sites.",
        icon="book-open",
        state_of=_instances_state("confluence"),
        test_targets=("confluence",),
    ),
    _settings_connector(
        "github",
        "GitHub",
        "Code & delivery",
        "Clone, push and open pull requests, plus the name and email on the assistant's commits.",
        icon="github",
        state_of=_github_state,
        config_sections=("github", "git"),
        form_sections=("github", "git"),
        test_targets=("github",),
        extra_guidance_keys=("git",),
    ),
    _settings_connector(
        "jenkins",
        "Jenkins",
        "Code & delivery",
        "Look up jobs, builds and deployments on one or more Jenkins servers.",
        icon="hammer",
        state_of=_instances_state("jenkins"),
        test_targets=("jenkins",),
    ),
    _settings_connector(
        "nexus",
        "Nexus Repository",
        "Code & delivery",
        "Find artifacts and image versions in Nexus.",
        icon="package",
        state_of=_instances_state("nexus"),
        test_targets=("nexus",),
    ),
    _settings_connector(
        "aws",
        "AWS",
        "Cloud & data",
        "Sign in to your AWS accounts with a read-only role and reach their EKS clusters.",
        icon="cloud",
        state_of=_aws_state,
    ),
    _settings_connector(
        "splunk",
        "Splunk",
        "Cloud & data",
        "Search service logs in Splunk.",
        icon="scroll-text",
        state_of=_instances_state("splunk"),
        test_targets=("splunk",),
    ),
    _settings_connector(
        "pgsql",
        "PostgreSQL",
        "Cloud & data",
        "Run read-only queries against your databases.",
        icon="database",
        state_of=_instances_state("pgsql", ("host",)),
        test_targets=("pgsql",),
    ),
    _settings_connector(
        "browserstack",
        "BrowserStack",
        "Testing",
        "Drive real mobile devices on BrowserStack for app tests.",
        icon="smartphone",
        state_of=_browserstack_state,
        config_sections=("mobile-auto",),
        form_sections=("mobile",),
        guidance_key="mobile",
    ),
    _settings_connector(
        "proxy",
        "Proxy",
        "Network",
        "Send your assistants' outbound traffic through an HTTP proxy.",
        icon="network",
        state_of=_proxy_state,
        test_targets=("proxy",),
    ),
)


CONNECTOR_REGISTRY: dict[str, ConnectorSpec] = {
    **{spec.type: spec for spec in SETTINGS_CONNECTORS},
    LOCAL_BROWSER_TYPE: ConnectorSpec(
        type=LOCAL_BROWSER_TYPE,
        label="Local browser",
        kind=KIND_LOCAL,
        icon="globe",
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
        settings_provider=_local_browser_settings,
    ),
}


def get_connector_spec(connector_type: str) -> ConnectorSpec:
    """Return the spec for ``connector_type``; raises KeyError when unknown."""

    return CONNECTOR_REGISTRY[str(connector_type or "").strip()]


def list_connector_specs(*, include_local: bool = True) -> list[ConnectorSpec]:
    """Every connector type in display order: by category, then registry order.

    ``include_local=False`` drops the connectors that run on the member's PC,
    which a deployment switches off with CONNECTORS_ENABLED=false.
    """

    specs = [spec for spec in CONNECTOR_REGISTRY.values() if include_local or spec.kind != KIND_LOCAL]
    order = {category: index for index, category in enumerate(CATEGORY_ORDER)}
    return sorted(specs, key=lambda spec: order.get(spec.category, len(order)))


def is_known_connector(connector_type: str) -> bool:
    return str(connector_type or "").strip() in CONNECTOR_REGISTRY


def settings_connector_for_section(section: str) -> ConnectorSpec | None:
    """The settings connector that owns config key or form section ``section``."""

    for spec in SETTINGS_CONNECTORS:
        if section in spec.config_sections or section in spec.form_sections:
            return spec
    return None


def state_label(spec: ConnectorSpec, state: str) -> str:
    if state == STATE_CONNECTED:
        return "On" if spec.type == "proxy" else "Connected"
    if state == STATE_OFF:
        return "Turned off"
    return "Not set up"


__all__ = [
    "CATEGORY_ORDER",
    "CONNECTOR_REGISTRY",
    "ConnectorSpec",
    "KIND_LOCAL",
    "KIND_SETTINGS",
    "SETTINGS_CONNECTORS",
    "STATE_CONNECTED",
    "STATE_NOT_SET_UP",
    "STATE_OFF",
    "LOCAL_BROWSER_DEFAULT_PORT",
    "LOCAL_BROWSER_PLATFORMS",
    "LOCAL_BROWSER_TYPE",
    "detect_local_browser_platform",
    "get_connector_spec",
    "is_known_connector",
    "list_connector_specs",
    "local_browser_platform_label",
    "settings_connector_for_section",
    "state_label",
]
