import pytest


def has_sqlalchemy() -> bool:
    try:
        import sqlalchemy  # noqa: F401
        return True
    except Exception:
        return False


def skip_if_missing_sqlalchemy(message: str = "sqlalchemy is required for this integration-style test") -> None:
    if not has_sqlalchemy():
        pytest.skip(message, allow_module_level=True)
