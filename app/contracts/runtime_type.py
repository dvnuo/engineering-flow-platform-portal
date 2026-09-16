import re
from collections.abc import Sequence

ALLOWED_RUNTIME_TYPES = ("native", "opencode")
DEFAULT_RUNTIME_TYPE = "native"

# Member-facing copy for each marker. The create wizard, the assistant-type
# editor and /api/agents/defaults all read it from here.
RUNTIME_TYPE_LABELS = {
    "native": "EFP Native Runtime",
    "opencode": "OpenCode Runtime",
}
RUNTIME_TYPE_DESCRIPTIONS = {
    "native": "Use the native EFP Python runtime.",
    "opencode": "Use the opencode runtime adapter.",
}


class InvalidRuntimeType(ValueError):
    pass


class RuntimeTypeNotEnabled(InvalidRuntimeType):
    """A supported marker that this Portal does not offer for new agents."""


def normalize_runtime_type(value: str | None, *, allow_default: bool = False) -> str:
    normalized = (value or "").strip().lower()
    if not normalized:
        if allow_default:
            return DEFAULT_RUNTIME_TYPE
        raise InvalidRuntimeType("runtime_type is required")
    if normalized not in ALLOWED_RUNTIME_TYPES:
        raise InvalidRuntimeType("runtime_type must be one of: native, opencode")
    return normalized


def normalize_runtime_type_or_default(value: str | None) -> str:
    try:
        return normalize_runtime_type(value, allow_default=True)
    except InvalidRuntimeType:
        return DEFAULT_RUNTIME_TYPE


def normalize_enabled_runtime_types(value: str | None) -> tuple[str, ...]:
    """Markers offered for new agents, from ENABLED_RUNTIME_TYPES.

    Comma-separated and case-insensitive. Unknown markers are dropped and the
    result keeps the ALLOWED_RUNTIME_TYPES order, so the UI is stable however
    the variable is written. Nothing valid offers the default marker so
    creation never dead-ends. Every marker in ALLOWED_RUNTIME_TYPES stays valid
    for existing agents regardless of this list.
    """
    requested = {token.lower() for token in re.split(r"[\s,;]+", value or "") if token}
    enabled = tuple(marker for marker in ALLOWED_RUNTIME_TYPES if marker in requested)
    return enabled or (DEFAULT_RUNTIME_TYPE,)


def pick_enabled_runtime_type(preferred: str | None, enabled: Sequence[str]) -> str:
    """The marker preselected for a new agent: ``preferred`` when it is offered,
    otherwise the first offered one."""
    normalized = normalize_runtime_type_or_default(preferred)
    if normalized in enabled:
        return normalized
    return enabled[0] if enabled else DEFAULT_RUNTIME_TYPE


def require_enabled_runtime_type(value: str | None, enabled: Sequence[str]) -> str:
    """Normalize ``value`` and reject it unless this Portal offers it for new agents."""
    normalized = normalize_runtime_type(value)
    if normalized not in enabled:
        raise RuntimeTypeNotEnabled(
            f"runtime_type '{normalized}' is not enabled on this Portal "
            f"(ENABLED_RUNTIME_TYPES={','.join(enabled)})"
        )
    return normalized
