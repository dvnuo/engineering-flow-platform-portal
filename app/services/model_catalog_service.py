"""The Portal-wide Copilot model catalog: one list for every member and assistant.

Copilot's catalog (``GET /models``) is read per credential, but the
organization's model policy is the same for every member, so the Portal keeps
one copy. The first Copilot assistant whose runtime answers ``GET /api/models``
with ``source: "catalog"`` fills it; every picker reads it (the composer
through ``chat-model-profile``, the connector form through
``GET /api/models/catalog``) and validation accepts exactly what it lists, so
nobody is offered a model the Portal then refuses. The copy lives in memory
and in ``platform_settings`` (key ``copilot_model_catalog``), so a Portal
restart does not show the static table first; the static table remains the
fallback until a runtime has answered. A fetch that fails is not retried for
a few minutes, and a running assistant is asked again once the copy is an
hour old.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.contracts.llm_catalog import (
    COPILOT_AUTO_TIERS,
    COPILOT_MODELS,
    COPILOT_PROVIDER,
    is_selectable_model,
    normalize_provider,
    register_catalog_models,
    clear_catalog_models,
)
from app.repositories.platform_setting_repo import PlatformSettingRepository
from app.services.proxy_service import ProxyService, build_portal_agent_headers

logger = logging.getLogger(__name__)

CATALOG_SETTING_KEY = "copilot_model_catalog"
CATALOG_TTL = timedelta(hours=1)
FAILURE_BACKOFF = timedelta(minutes=5)
FETCH_TIMEOUT_SECONDS = 5.0
SOURCE_CATALOG = "catalog"
SOURCE_STATIC = "static"
AUTO_LABELS = {
    "auto": "Auto",
    "auto-efficiency": "Auto (Efficiency)",
    "auto-intelligence": "Auto (Intelligence)",
}
_ENTRY_KEYS = ("vendor", "category", "limits", "supports", "billing_threshold_tokens", "known", "tier")

proxy_service = ProxyService()

_lock = threading.Lock()
_state: dict[str, Any] = {"catalog": None, "loaded_from_db": False, "failed_at": None}


@dataclass(frozen=True)
class ModelCatalog:
    """The list the pickers show: auto entries first, then the selectable models."""

    source: str
    fetched_at: str | None
    models: tuple[dict[str, Any], ...]
    # When the runtime itself last read Copilot's catalog (its own hourly
    # cache); ``fetched_at`` is when the Portal stored this copy, which is
    # what its freshness is measured by.
    runtime_fetched_at: str | None = None

    def ids(self) -> tuple[str, ...]:
        return tuple(str(entry["id"]) for entry in self.models)

    def labels(self) -> dict[str, str]:
        return {str(entry["id"]): str(entry.get("label") or entry["id"]) for entry in self.models}

    def is_fresh(self, now: datetime | None = None) -> bool:
        if self.source != SOURCE_CATALOG or not self.fetched_at:
            return False
        stamp = _parse_iso(self.fetched_at)
        if stamp is None:
            return False
        return (now or datetime.now(timezone.utc)) - stamp < CATALOG_TTL

    def payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "fetched_at": self.fetched_at,
            "runtime_fetched_at": self.runtime_fetched_at,
            "stale": self.source == SOURCE_CATALOG and not self.is_fresh(),
            "provider": COPILOT_PROVIDER,
            "models": [dict(entry) for entry in self.models],
            "model_labels": self.labels(),
        }

    def stored(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "fetched_at": self.fetched_at,
            "runtime_fetched_at": self.runtime_fetched_at,
            "provider": COPILOT_PROVIDER,
            "models": [dict(entry) for entry in self.models],
        }


def static_catalog() -> ModelCatalog:
    """The hand-maintained list, shaped like the catalog."""

    models = tuple(
        {"id": model_id, "label": AUTO_LABELS.get(model_id, model_id), "kind": "auto" if model_id in COPILOT_AUTO_TIERS else "model"}
        for model_id in COPILOT_MODELS
    )
    return ModelCatalog(source=SOURCE_STATIC, fetched_at=None, models=models)


def project_runtime_models(payload: Any) -> dict[str, Any] | None:
    """The runtime's ``GET /api/models`` answer as the Portal stores it, or None when it is not a catalog.

    Only ``source: "catalog"`` counts (a runtime's static fallback is the
    Portal's own static list); the auto entries come first, then the rows the
    runtime marked selectable with the name Copilot gives them.
    """

    if not isinstance(payload, Mapping) or payload.get("available") is not True:
        return None
    if str(payload.get("source") or "") != SOURCE_CATALOG:
        return None
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in payload.get("auto") or []:
        if not isinstance(item, Mapping):
            continue
        auto_id = str(item.get("id") or "").strip().lower()
        if not auto_id or auto_id in seen:
            continue
        seen.add(auto_id)
        entries.append(
            {
                "id": auto_id,
                "label": str(item.get("label") or AUTO_LABELS.get(auto_id, auto_id)).strip() or auto_id,
                "kind": "auto",
                "tier": item.get("tier") if isinstance(item.get("tier"), str) else None,
            }
        )
    model_count = 0
    for row in payload.get("models") or []:
        if not isinstance(row, Mapping) or row.get("selectable") is not True:
            continue
        model_id = str(row.get("id") or "").strip().lower()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        entry: dict[str, Any] = {
            "id": model_id,
            "label": str(row.get("name") or model_id).strip() or model_id,
            "kind": "model",
        }
        for key in _ENTRY_KEYS:
            if key in row and row.get(key) is not None:
                entry[key] = row.get(key)
        entries.append(entry)
        model_count += 1
    if model_count == 0:
        return None
    return {
        "source": SOURCE_CATALOG,
        "fetched_at": _now_iso(),
        "runtime_fetched_at": str(payload.get("fetched_at") or "").strip() or None,
        "provider": COPILOT_PROVIDER,
        "models": entries,
    }


def current_catalog(db: Session | None = None) -> ModelCatalog:
    """The Portal-wide catalog: memory, else the stored copy (once per process), else the static list."""

    with _lock:
        catalog = _state["catalog"]
        loaded = _state["loaded_from_db"]
    if catalog is None and db is not None and not loaded:
        catalog = _load_stored(db)
        with _lock:
            _state["loaded_from_db"] = True
            if catalog is not None and _state["catalog"] is None:
                _state["catalog"] = catalog
        if catalog is not None:
            register_catalog_models(catalog.ids())
    return catalog or static_catalog()


def store_catalog(db: Session | None, projected: Mapping[str, Any], *, updated_by_user_id: int | None = None) -> ModelCatalog:
    """Make ``projected`` (see ``project_runtime_models``) the catalog, in memory and in platform_settings."""

    catalog = _catalog_from_stored(projected)
    if catalog is None:
        raise ValueError("not a model catalog")
    with _lock:
        _state["catalog"] = catalog
        _state["loaded_from_db"] = True
        _state["failed_at"] = None
    register_catalog_models(catalog.ids())
    if db is not None:
        try:
            PlatformSettingRepository(db).set_value(CATALOG_SETTING_KEY, catalog.stored(), updated_by_user_id=updated_by_user_id)
        except Exception as exc:  # noqa: BLE001 - the in-memory copy still serves this process.
            logger.warning("Copilot model catalog not persisted: %s", exc.__class__.__name__)
    return catalog


def reset_catalog_state() -> None:
    """Forget the catalog (tests, and an admin asking for a fresh fetch)."""

    with _lock:
        _state["catalog"] = None
        _state["loaded_from_db"] = False
        _state["failed_at"] = None
    clear_catalog_models()


def is_model_allowed(provider: str | None, model: str | None, *, db: Session | None = None) -> bool:
    """Whether a member may pick ``model`` on ``provider``: the static list, or what the Portal-wide catalog lists.

    With ``db`` a cold process loads the stored catalog first, so validation
    agrees with the picker the member saw. Without a catalog yet, a plausible
    non-retired Copilot id passes and the runtime has the final word.
    """

    if normalize_provider(provider) == COPILOT_PROVIDER:
        current_catalog(db)
    return is_selectable_model(provider, model)


async def fetch_runtime_models(*, user: Any, agent: Any) -> Mapping[str, Any] | None:
    """``GET /api/models`` from the assistant's runtime, or None when it cannot be asked or does not answer."""

    try:
        answer = await asyncio.wait_for(
            proxy_service.forward(
                agent=agent,
                method="GET",
                subpath="api/models",
                query_items=[],
                body=None,
                headers={},
                extra_headers=build_portal_agent_headers(user, agent),
            ),
            timeout=FETCH_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - a runtime that does not answer leaves the current list.
        logger.debug("Copilot model catalog not fetched from agent %s: %s", getattr(agent, "id", "?"), exc.__class__.__name__)
        return None
    status_code, content = answer[0], answer[1]
    if status_code != 200:
        return None
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return payload if isinstance(payload, Mapping) else None


async def ensure_catalog(db: Session | None, *, user: Any, agent: Any, provider: str | None = None) -> ModelCatalog:
    """The catalog, refreshed from ``agent``'s runtime when it is missing or an hour old.

    Only a running Copilot assistant is asked; a failed fetch is not retried
    for ``FAILURE_BACKOFF``. Whatever happens, the current list is returned.
    """

    catalog = current_catalog(db)
    if provider is not None and normalize_provider(provider) != COPILOT_PROVIDER:
        return catalog
    if catalog.source == SOURCE_CATALOG and catalog.is_fresh():
        return catalog
    if agent is None or str(getattr(agent, "status", "") or "").strip().lower() != "running":
        return catalog
    with _lock:
        failed_at = _state["failed_at"]
    if failed_at is not None and datetime.now(timezone.utc) - failed_at < FAILURE_BACKOFF:
        return catalog
    payload = await fetch_runtime_models(user=user, agent=agent)
    projected = project_runtime_models(payload) if payload is not None else None
    if projected is None:
        with _lock:
            _state["failed_at"] = datetime.now(timezone.utc)
        return catalog
    return store_catalog(db, projected, updated_by_user_id=getattr(user, "id", None))


def _load_stored(db: Session) -> ModelCatalog | None:
    try:
        stored = PlatformSettingRepository(db).get_value(CATALOG_SETTING_KEY, {})
    except Exception as exc:  # noqa: BLE001 - no stored copy is the same as none.
        logger.debug("Copilot model catalog not loaded from platform settings: %s", exc.__class__.__name__)
        return None
    return _catalog_from_stored(stored)


def _catalog_from_stored(stored: Any) -> ModelCatalog | None:
    if not isinstance(stored, Mapping) or str(stored.get("source") or "") != SOURCE_CATALOG:
        return None
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in stored.get("models") or []:
        if not isinstance(entry, Mapping):
            continue
        model_id = str(entry.get("id") or "").strip().lower()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        clean = {key: value for key, value in entry.items() if isinstance(key, str)}
        clean["id"] = model_id
        clean["label"] = str(entry.get("label") or model_id).strip() or model_id
        models.append(clean)
    if not any(entry.get("kind") != "auto" for entry in models):
        return None
    fetched_at = str(stored.get("fetched_at") or "").strip() or None
    runtime_fetched_at = str(stored.get("runtime_fetched_at") or "").strip() or None
    return ModelCatalog(source=SOURCE_CATALOG, fetched_at=fetched_at, models=tuple(models), runtime_fetched_at=runtime_fetched_at)


def _parse_iso(value: str) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "CATALOG_SETTING_KEY",
    "CATALOG_TTL",
    "FAILURE_BACKOFF",
    "ModelCatalog",
    "current_catalog",
    "ensure_catalog",
    "fetch_runtime_models",
    "is_model_allowed",
    "project_runtime_models",
    "reset_catalog_state",
    "static_catalog",
    "store_catalog",
]
