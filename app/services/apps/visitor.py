"""The visitor header an app passes on with an SDK call (docs/CONTRACTS.md, section 6.4).

The gateway signs ``X-EFP-Visitor`` for the member who is using an app, with
the app's visitor key; the app may send the value it received along with an
SDK call so the Portal can record who caused it. Here the value is only ever
attribution: it is checked for a genuine signature made for this app, and
anything else simply means "no visitor". The expiry is not checked, on
purpose: the value lives sixty seconds, and an app that keeps one page open
(Streamlit) forwards a value that is minutes old with every call.

The app holds the same key (``EFP_VISITOR_KEY``), so the value says who the
app says caused the call. It never decides what the call may do.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from typing import Any, Optional

HEADER = "X-EFP-Visitor"
VERSION = "v1"
MAX_HEADER_LENGTH = 4096

_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _key_bytes(visitor_key: Any) -> Optional[bytes]:
    if not isinstance(visitor_key, str) or not visitor_key.strip():
        return None
    try:
        decoded = _b64decode(visitor_key.strip().rstrip("="))
    except (binascii.Error, ValueError):
        return None
    return decoded or None


def visitor_claims(header_value: Any, visitor_key: Any, app_id: str) -> Optional[dict]:
    """The payload of a header value that is genuinely signed for ``app_id``; None for anything else."""
    if not isinstance(header_value, str):
        return None
    value = header_value.strip()
    if not value or len(value) > MAX_HEADER_LENGTH:
        return None
    key = _key_bytes(visitor_key)
    if key is None:
        return None
    parts = value.split(".")
    if len(parts) != 3 or parts[0] != VERSION or not _B64URL.match(parts[1]) or not _B64URL.match(parts[2]):
        return None
    try:
        presented = _b64decode(parts[2])
        expected = hmac.new(key, f"{VERSION}.{parts[1]}".encode("ascii"), hashlib.sha256).digest()
    except (binascii.Error, ValueError):
        return None
    if not hmac.compare_digest(presented, expected):
        return None
    try:
        claims = json.loads(_b64decode(parts[1]).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(claims, dict) or claims.get("app") != app_id:
        return None
    return claims


def visitor_user_id(header_value: Any, visitor_key: Any, app_id: str) -> Optional[int]:
    """The Portal user id a genuine header names; None when there is no header or it is not genuine."""
    claims = visitor_claims(header_value, visitor_key, app_id)
    if claims is None:
        return None
    subject = claims.get("sub")
    if isinstance(subject, bool) or not isinstance(subject, int) or subject < 1:
        return None
    return subject
