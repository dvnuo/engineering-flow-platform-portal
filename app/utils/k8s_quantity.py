"""Kubernetes resource quantities, as far as the assistant panel needs them.

The cluster reports CPU and memory as quantity strings in several notations
(``250m`` and ``1`` for CPU, ``512Mi`` and ``2Gi`` for memory, and whatever the
metrics API picks: ``183240n``, ``734224Ki``). The panel compares a reading with
its limit and shows both in one notation, so this module turns a quantity into
a number and a number back into the short form people already know from the
settings. Anything it cannot read comes back as None and is shown as-is.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Optional

_BINARY_SUFFIXES = {
    "Ki": Decimal(1024),
    "Mi": Decimal(1024) ** 2,
    "Gi": Decimal(1024) ** 3,
    "Ti": Decimal(1024) ** 4,
    "Pi": Decimal(1024) ** 5,
    "Ei": Decimal(1024) ** 6,
}
_DECIMAL_SUFFIXES = {
    "n": Decimal("1e-9"),
    "u": Decimal("1e-6"),
    "m": Decimal("1e-3"),
    "k": Decimal("1e3"),
    "M": Decimal("1e6"),
    "G": Decimal("1e9"),
    "T": Decimal("1e12"),
    "P": Decimal("1e15"),
    "E": Decimal("1e18"),
}
_MEBIBYTE = 1024 * 1024
_GIBIBYTE = 1024 * _MEBIBYTE


def parse_quantity(value: Any) -> Optional[Decimal]:
    """The quantity as a plain number (cores for CPU, bytes for memory), or None."""

    text = str(value if value is not None else "").strip()
    if not text:
        return None
    number_text, factor = text, Decimal(1)
    if text[-2:] in _BINARY_SUFFIXES:
        number_text, factor = text[:-2], _BINARY_SUFFIXES[text[-2:]]
    elif text[-1:] in _DECIMAL_SUFFIXES:
        number_text, factor = text[:-1], _DECIMAL_SUFFIXES[text[-1:]]
    try:
        number = Decimal(number_text)
    except InvalidOperation:
        return None
    if not number.is_finite() or number < 0:
        return None
    return number * factor


def cpu_millicores(value: Any) -> Optional[int]:
    quantity = parse_quantity(value)
    if quantity is None:
        return None
    return int((quantity * 1000).to_integral_value())


def memory_bytes(value: Any) -> Optional[int]:
    quantity = parse_quantity(value)
    if quantity is None:
        return None
    return int(quantity.to_integral_value())


def format_cpu(millicores: int) -> str:
    """``250m`` below one core, ``1.5`` from one core up: how the settings write it."""

    if millicores < 1000:
        return f"{millicores}m"
    return _trim(f"{millicores / 1000:.2f}")


def format_memory(byte_count: int) -> str:
    """Whole mebibytes below one gibibyte, gibibytes with one decimal above."""

    if byte_count < _GIBIBYTE:
        return f"{int(round(byte_count / _MEBIBYTE))}Mi"
    return _trim(f"{byte_count / _GIBIBYTE:.1f}") + "Gi"


def percent_of(part: Optional[int], whole: Optional[int]) -> Optional[int]:
    if part is None or not whole or whole <= 0:
        return None
    return int(round(part * 100 / whole))


def _trim(text: str) -> str:
    return text.rstrip("0").rstrip(".") if "." in text else text
