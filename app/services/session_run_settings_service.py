"""Run settings a member chose for one chat session.

The model, thinking level and context size picked under "Run settings" in
the chat composer used to live in the page: a refresh, another tab or another
device went back to the assistant's defaults, and switching sessions carried
the previous session's choice along. They now live with the session, in
``agent_session_metadata.run_settings_json`` (a JSON object with
``model_override``, ``reasoning_effort`` and ``max_context_tokens``; absent
keys mean the assistant's default). The member writes them through the Portal
API, the chat UI restores them when it opens the session, and the chat proxy
fills them into a request that names none of its own.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.repositories.agent_session_metadata_repo import AgentSessionMetadataRepository
from app.services.inference_settings_service import normalize_agent_inference_overrides

logger = logging.getLogger(__name__)

RUN_SETTING_KEYS = ("model_override", "reasoning_effort", "max_context_tokens")


def parse_run_settings_json(raw: Any) -> dict[str, Any]:
    """The stored JSON as a dict of the known keys with usable values, {} otherwise."""

    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    settings: dict[str, Any] = {}
    model = data.get("model_override")
    if isinstance(model, str) and model.strip():
        settings["model_override"] = model.strip()
    effort = data.get("reasoning_effort")
    if isinstance(effort, str) and effort.strip():
        settings["reasoning_effort"] = effort.strip().lower()
    tokens = data.get("max_context_tokens")
    if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens > 0:
        settings["max_context_tokens"] = tokens
    return settings


def load_session_run_settings(db: Session, agent_id: str, session_id: str) -> dict[str, Any]:
    """What is stored for the session: only the known keys, {} when nothing is."""

    record = AgentSessionMetadataRepository(db).get_by_agent_and_session(agent_id=agent_id, session_id=session_id)
    return parse_run_settings_json(getattr(record, "run_settings_json", None))


def save_session_run_settings(db: Session, agent, session_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the values against the agent's current profile and store them.

    Raises ValueError for a model, effort or context size the profile does
    not offer (the same rule the chat request itself is held to). A value of
    None, or an empty one, clears that setting; with every setting cleared
    the row holds no run settings at all.
    """

    normalized = normalize_agent_inference_overrides(db, agent, {key: values.get(key) for key in RUN_SETTING_KEYS})
    stored: dict[str, Any] = {}
    if normalized.get("model"):
        stored["model_override"] = normalized["model"]
    if normalized.get("reasoning_effort"):
        stored["reasoning_effort"] = normalized["reasoning_effort"]
    if normalized.get("max_context_tokens"):
        stored["max_context_tokens"] = normalized["max_context_tokens"]
    AgentSessionMetadataRepository(db).upsert(
        agent_id=agent.id,
        session_id=session_id,
        run_settings_json=json.dumps(stored, sort_keys=True) if stored else None,
    )
    return stored


def apply_session_run_settings(db: Session, agent, payload: Mapping[str, Any]) -> dict[str, Any]:
    """A chat payload with the session's stored settings, when it names none of its own.

    All or nothing: a request that carries any of the three keys (the
    composer sends what differs from the defaults, and an explicit null means
    the default) is the member's current choice and is left alone. Stored
    values the profile no longer offers are ignored rather than failing the
    chat.
    """

    merged = dict(payload)
    if any(key in merged for key in RUN_SETTING_KEYS):
        return merged
    session_id = str(merged.get("session_id") or "").strip()
    agent_id = str(getattr(agent, "id", "") or "")
    if not session_id or not agent_id:
        return merged
    # A saved convenience must never fail the chat itself: a store that
    # cannot be read, or holds values the profile no longer offers, is
    # simply not applied.
    try:
        stored = load_session_run_settings(db, agent_id, session_id)
        if not stored:
            return merged
        normalize_agent_inference_overrides(db, agent, stored)
    except ValueError as exc:
        logger.info("run settings stored for session %s of agent %s no longer apply, ignored: %s", session_id, agent_id, exc)
        return merged
    except Exception as exc:  # noqa: BLE001 - see above.
        logger.warning("run settings for session %s of agent %s could not be read, ignored: %s", session_id, agent_id, exc)
        return merged
    merged.update(stored)
    return merged


__all__ = [
    "RUN_SETTING_KEYS",
    "apply_session_run_settings",
    "load_session_run_settings",
    "parse_run_settings_json",
    "save_session_run_settings",
]
