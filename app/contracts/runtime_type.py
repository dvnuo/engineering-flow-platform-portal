ALLOWED_RUNTIME_TYPES = ("native",)
DEFAULT_RUNTIME_TYPE = "native"


class InvalidRuntimeType(ValueError):
    pass


def normalize_runtime_type(value: str | None, *, allow_default: bool = False) -> str:
    normalized = (value or "").strip().lower()
    if not normalized:
        if allow_default:
            return DEFAULT_RUNTIME_TYPE
        raise InvalidRuntimeType("runtime_type is required")
    if normalized not in ALLOWED_RUNTIME_TYPES:
        raise InvalidRuntimeType("runtime_type is fixed to native")
    return normalized


def normalize_runtime_type_or_default(value: str | None) -> str:
    try:
        return normalize_runtime_type(value, allow_default=True)
    except InvalidRuntimeType:
        return DEFAULT_RUNTIME_TYPE
