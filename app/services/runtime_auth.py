"""Portal-to-runtime request authentication helpers."""

from __future__ import annotations

import hashlib
import hmac


def derive_runtime_internal_token(secret_key: object, agent_id: object) -> str:
    """Derive an agent-scoped token without exposing the Portal master secret."""
    secret = str(secret_key or "").strip()
    normalized_agent_id = str(agent_id or "").strip()
    if not secret or not normalized_agent_id:
        return ""
    message = f"efp-runtime-proxy:{normalized_agent_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
