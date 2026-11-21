"""A secret the Portal shares with each assistant's runtime, and with nothing else.

Inside the cluster a runtime used to believe any request that said
``X-Portal-Author-Source: portal``, and the Portal's ``/api/internal/*``
believed any caller. Once member-written code runs in the same cluster,
both are forgeable. The token closes that in both directions:

- The Portal derives one token per assistant from its own ``SECRET_KEY``:
  ``HMAC-SHA256(key=SECRET_KEY, message="agent-trust:v1:" + agent_id)``, hex.
  Nothing is stored; the same inputs always give the same token.
- It reaches the pod as the environment variable ``EFP_PORTAL_TRUST_TOKEN``
  (app/services/k8s_service.py).
- The Portal sends it as ``X-Portal-Trust-Token`` on every request it makes
  to that runtime, the events WebSocket handshake included. A runtime that
  has the variable rejects "Portal" requests without the matching header.
- The runtime sends the same header on its calls to ``/api/internal/*``,
  where the Portal checks it against the assistant named in the path
  (app/security/internal_api.py).

A token is only as secret as ``SECRET_KEY``: with the built-in default key
anyone can compute it (app/security/startup_checks.py). Changing
``SECRET_KEY`` changes every token, so assistants must be restarted after it
to receive the new one.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any
import uuid

from app.config import get_settings

TRUST_TOKEN_HEADER = "X-Portal-Trust-Token"
TRUST_TOKEN_ENV = "EFP_PORTAL_TRUST_TOKEN"
_MESSAGE_PREFIX = "agent-trust:v1:"


def agent_trust_token(agent_id: Any) -> str:
    """The hex token for the assistant with this id."""
    key = str(get_settings().secret_key or "").encode("utf-8")
    message = (_MESSAGE_PREFIX + str(agent_id)).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def trust_token_matches(agent_id: Any, presented: Any) -> bool:
    """Whether ``presented`` is the token of the assistant with this id (constant-time)."""
    if agent_id is None or not isinstance(presented, str) or not presented:
        return False
    expected = agent_trust_token(agent_id)
    return hmac.compare_digest(expected.encode("utf-8"), presented.strip().encode("utf-8"))


def agent_trust_headers(agent: Any) -> dict[str, str]:
    """``{X-Portal-Trust-Token: ...}`` for a request to this assistant's runtime.

    ``agent`` is the assistant row or its id. Empty for an assistant without
    an id, which does not exist outside tests.
    """
    agent_id = agent if isinstance(agent, (str, int, uuid.UUID)) else getattr(agent, "id", None)
    if agent_id is None or isinstance(agent_id, bool) or str(agent_id).strip() == "":
        return {}
    return {TRUST_TOKEN_HEADER: agent_trust_token(agent_id)}
