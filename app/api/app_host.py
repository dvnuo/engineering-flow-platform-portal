"""The Portal's API for the app host (docs/CONTRACTS.md, section 5): ``/api/app-host/v1``.

The host calls these from inside the cluster with the shared bearer token
(``APPS_HOST_API_TOKEN``); the ingress never routes them. An empty configured
token rejects every call, and every route answers 404 while ``APPS_ENABLED``
is off. Errors use the contract's envelope: ``{"error": {"code", "message"}}``.

- ``POST /handoff/redeem``: turn the one-time code of a launch into the app and the member.
- ``POST /access/check``: whether a member may (still) open an app.
- ``GET  /apps``: the desired state, read when the host boots.
- ``POST /metrics/ingest``: traffic counters, every app's status, the host's own figures.
- ``POST /events``: something happened to an app (it crashed, was killed, went idle ...).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import hmac
import json
import logging
import re
from typing import Any, Callable, Optional

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.app import App
from app.repositories.app_repo import AppRepository, AppTrafficRepository
from app.repositories.user_repo import UserRepository
from app.services.apps import settings as apps_settings
from app.services.apps.access import app_enabled, can_open, handoff_codes, is_member_active, user_payload
from app.services.apps.audit import record_host_event
from app.services.apps.error_events import record_host_crash
from app.services.apps.host_spec import desired_app
from app.services.apps.host_state import host_state

logger = logging.getLogger(__name__)

REASON_NO_ACCESS = "no_access"
REASON_USER_INACTIVE = "user_inactive"
REASON_APP_DISABLED = "app_disabled"
REASON_APP_NOT_FOUND = "app_not_found"

EVENT_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
EVENT_DETAIL_MAX_CHARS = 16000
MAX_TRAFFIC_ITEMS = 5000
MAX_USERS_PER_ITEM = 5000
COUNTER_MAX = 2_000_000_000


class HostApiError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _envelope(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status_code)


class HostApiRoute(APIRoute):
    """Answers every failure of these routes in the contract's error envelope."""

    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def host_api_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except HostApiError as exc:
                return _envelope(exc.code, exc.message, exc.status_code)
            except RequestValidationError:
                return _envelope("invalid_request", "The request body is not what this endpoint expects.", 400)

        return host_api_handler


def require_app_host(request: Request) -> None:
    """The shared bearer token, compared in constant time. No configured token, no access."""
    if not apps_settings.apps_enabled():
        raise HostApiError("not_found", "Apps are not enabled on this Portal.", 404)
    configured = apps_settings.host_api_token()
    scheme, _, presented = (request.headers.get("authorization") or "").partition(" ")
    presented = presented.strip()
    if (
        not configured
        or scheme.lower() != "bearer"
        or not presented
        or not hmac.compare_digest(presented.encode("utf-8"), configured.encode("utf-8"))
    ):
        raise HostApiError("unauthorized", "A valid host API token is required.", 401)


router = APIRouter(
    prefix="/api/app-host/v1",
    tags=["app-host"],
    route_class=HostApiRoute,
    dependencies=[Depends(require_app_host)],
)


def _object(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise HostApiError("invalid_request", "The request body must be a JSON object.", 400)
    return payload


def _int(value: Any, *, minimum: int = 0, maximum: int = COUNTER_MAX) -> Optional[int]:
    """A whole number in range, or None for anything else (booleans included)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value == int(value):
        value = int(value)
    if not isinstance(value, int) or value < minimum:
        return None
    return min(value, maximum)


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """An ISO-8601 instant as a naive UTC datetime (what the Portal stores); None when it is not one."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


# --- 5.1 ---------------------------------------------------------------------------


@router.post("/handoff/redeem")
def redeem_handoff(payload: Any = Body(default=None), db: Session = Depends(get_db)):
    grant = handoff_codes.redeem(_object(payload).get("code"))
    if grant is None:
        raise HostApiError("invalid_code", "The code is not valid: it was used already, it expired, or it never existed.", 400)
    app = AppRepository(db).get_by_id(grant.app_id)
    user = UserRepository(db).get_by_id(grant.user_id)
    # A minute is long enough for the member or the app to have changed.
    if app is None or user is None or not is_member_active(db, user) or not can_open(user, app, db):
        raise HostApiError("invalid_code", "The code is not valid: it was used already, it expired, or it never existed.", 400)
    return {"app_id": app.id, "slug": app.slug, "user": user_payload(db, user)}


# --- 5.2 ---------------------------------------------------------------------------


@router.post("/access/check")
def check_access(payload: Any = Body(default=None), db: Session = Depends(get_db)):
    body = _object(payload)
    app_id = body.get("app_id")
    user_id = _int(body.get("user_id"), minimum=1)
    if not isinstance(app_id, str) or not app_id.strip() or user_id is None:
        raise HostApiError("invalid_request", "app_id and user_id are required.", 400)

    user = UserRepository(db).get_by_id(user_id)
    answer: dict[str, Any] = {"allowed": False, "reason": None}
    if user is not None:
        answer["user"] = user_payload(db, user)
    app = AppRepository(db).get_by_id(app_id.strip())
    if app is None:
        answer["reason"] = REASON_APP_NOT_FOUND
    elif user is None or not is_member_active(db, user):
        answer["reason"] = REASON_USER_INACTIVE
    elif not app_enabled(db, app)[0]:
        answer["reason"] = REASON_APP_DISABLED
    elif not can_open(user, app, db):
        answer["reason"] = REASON_NO_ACCESS
    else:
        answer["allowed"] = True
    return answer


# --- 5.3 ---------------------------------------------------------------------------


@router.get("/apps")
def desired_state(db: Session = Depends(get_db)):
    apps = [desired_app(db, app) for app in AppRepository(db).list_all()]
    # The answer carries every app's token and visitor key.
    return JSONResponse({"apps": apps}, headers={"Cache-Control": "no-store"})


# --- 5.4 ---------------------------------------------------------------------------


@router.post("/metrics/ingest")
def ingest_metrics(payload: Any = Body(default=None), db: Session = Depends(get_db)):
    """Store the day's counters (running totals: the same post twice changes nothing, and a smaller one lowers nothing)."""
    body = _object(payload)
    traffic = body.get("traffic")
    items = traffic[:MAX_TRAFFIC_ITEMS] if isinstance(traffic, list) else []
    known = set(db.scalars(select(App.id)).all())
    repo = AppTrafficRepository(db)
    stored = 0
    try:
        for item in items:
            if not isinstance(item, dict):
                continue
            app_id = item.get("app_id")
            try:
                day = date.fromisoformat(str(item.get("day") or ""))
            except ValueError:
                continue
            if not isinstance(app_id, str) or app_id not in known:
                continue
            repo.upsert_day(
                day,
                app_id,
                requests=_int(item.get("requests")) or 0,
                visitors=_int(item.get("visitors")) or 0,
                errors_4xx=_int(item.get("errors_4xx")) or 0,
                errors_5xx=_int(item.get("errors_5xx")) or 0,
                p95_ms=_int(item.get("p95_ms")) or 0,
            )
            stored += 1
            users = item.get("users")
            for entry in users[:MAX_USERS_PER_ITEM] if isinstance(users, list) else []:
                if not isinstance(entry, dict):
                    continue
                user_id = _int(entry.get("user_id"), minimum=1)
                if user_id is None:
                    continue
                repo.upsert_visit(
                    app_id,
                    user_id,
                    day=day,
                    requests=_int(entry.get("requests")) or 0,
                    last_at=_parse_timestamp(entry.get("last_at")),
                )
        db.commit()
    except Exception:
        db.rollback()
        raise
    host_state.record_statuses(body.get("apps"))
    host_state.record_host(body.get("host"))
    return {"ok": True}


# --- 5.5 ---------------------------------------------------------------------------


def _bounded_detail(detail: Any) -> Any:
    if detail is None:
        return None
    try:
        encoded = json.dumps(detail, ensure_ascii=True, default=str)
    except (TypeError, ValueError):
        return {"truncated": True}
    if len(encoded) <= EVENT_DETAIL_MAX_CHARS:
        return detail
    return {"truncated": True, "text": encoded[:EVENT_DETAIL_MAX_CHARS]}


@router.post("/events")
def post_event(payload: Any = Body(default=None), db: Session = Depends(get_db)):
    body = _object(payload)
    app_id = body.get("app_id")
    kind = body.get("kind")
    if not isinstance(app_id, str) or not isinstance(kind, str) or not EVENT_KIND_RE.match(kind):
        raise HostApiError("invalid_request", "app_id and kind are required.", 400)
    app = AppRepository(db).get_by_id(app_id.strip(), include_deleted=True)
    if app is None:
        raise HostApiError(REASON_APP_NOT_FOUND, "No app has this id.", 404)
    detail = _bounded_detail(body.get("detail"))
    record_host_event(db, app.id, kind, detail, body.get("at"))
    # A crash also shows in Administration > Errors, thinned out to one entry per app in ten minutes.
    record_host_crash(db, app, kind, detail, body.get("at"))
    return {"ok": True}
