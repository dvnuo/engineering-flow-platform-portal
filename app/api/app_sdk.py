"""The SDK API (docs/CONTRACTS.md, section 9): ``/api/app-sdk/v1``.

What an app's own backend asks the Portal for, with the app's token and
nothing else: no cookie is ever read here and no CORS header is ever sent, so
a page in a browser cannot call it. Reachable from the cluster and through
the Portal's public address.

- ``GET  /app``                        the app, and what it may use.
- ``POST /llm/complete``               a text completion on the owner's model provider.
- ``GET  /connectors``                 the owner's connectors the app may call.
- ``POST /connectors/{type}/request``  one HTTP request to one of them.
- ``POST /images/inspect``             a question about images, on the owner's Image analysis connector.

Every failure, a malformed body and an unknown path below the prefix
included, answers ``{"error": {"code", "message"}}``; every route answers 404
while ``APPS_ENABLED`` is off. Each call is checked in this order: the token
(``invalid_token``), the app (``app_disabled``), the app's rate
(``rate_limited``), then what the route itself asks for.

A call the app was allowed to make leaves one ``app_executions`` row, however
it ended (app/services/apps/executions.py); a call refused at one of the gates
(the token, the rate, a capability that is off, the daily budget) leaves none.
Neither does an image call whose body the contract does not allow: that one
is checked whole before anything is counted, read from the database or sent.

No pooled database connection is held while a provider or a connector is
being waited for, nor while the body of an image call arrives: what a call
needs from the database is read first, the request's session is given back,
and the call's row is written afterwards through a session of its own, off
the event loop.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import logging
import re
import threading
import time
from typing import Any, AsyncIterator, Callable, Optional
import weakref

import anyio
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
import httpx
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from app.db import get_db
from app.models.app import App
from app.models.user import User
from app.services import model_catalog_service
from app.services.app_sdk import connector_proxy, image_analysis, llm_gateway
from app.services.app_sdk.connector_proxy import ConnectorProxyError
from app.services.app_sdk.llm_types import LlmGatewayError
from app.services.apps import budget as app_budget
from app.services.apps import settings as apps_settings
from app.services.apps import tokens as app_tokens
from app.services.apps.access import app_enabled, user_payload
from app.services.apps.executions import (
    ERROR_CLIENT_CLOSED,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    estimate_tokens,
    record_execution,
)
from app.services.apps.names import APP_CONNECTOR_TYPES, APP_IMAGE_ANALYSIS, DISABLED_OWNER_INACTIVE, has_capability
from app.services.apps.rate_limit import sdk_rate_limiter
from app.services.apps.visitor import HEADER as VISITOR_HEADER
from app.services.apps.visitor import visitor_user_id
from app.services.runtime_profile_service import RuntimeProfileService

logger = logging.getLogger(__name__)

ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
LOG_PATH_LIMIT = 200
ERROR_INTERNAL = "internal_error"

_LOG_UNSAFE = re.compile(r"[^\x21-\x7e]")


# --- errors --------------------------------------------------------------------------


class SdkApiError(Exception):
    """A failure of the SDK API: a code and a status of docs/CONTRACTS.md section 9."""

    def __init__(self, code: str, message: str, status_code: int, *, retry_after: Optional[int] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after


def _envelope(code: str, message: str, status_code: int, *, retry_after: Optional[int] = None) -> JSONResponse:
    headers: dict[str, str] = {}
    if retry_after is not None:
        headers["Retry-After"] = str(max(0, int(retry_after)))
    if status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status_code, headers=headers)


class SdkApiRoute(APIRoute):
    """Answers every failure of these routes in the contract's error envelope."""

    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def sdk_api_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except SdkApiError as exc:
                return _envelope(exc.code, exc.message, exc.status_code, retry_after=exc.retry_after)
            except RequestValidationError:
                return _envelope("invalid_request", "The request body is not what this endpoint expects.", 400)
            except HTTPException as exc:
                code = "not_found" if exc.status_code == 404 else "invalid_request" if exc.status_code < 500 else ERROR_INTERNAL
                return _envelope(code, str(exc.detail or "The request could not be completed."), exc.status_code)
            except Exception:  # noqa: BLE001 - the envelope is the contract for every failure
                logger.exception("app sdk: %s %s failed unexpectedly", request.method, request.url.path)
                return _envelope(ERROR_INTERNAL, "The Portal could not complete the request.", 500)

        return sdk_api_handler


router = APIRouter(prefix="/api/app-sdk/v1", tags=["app-sdk"], route_class=SdkApiRoute)


# --- what tests replace -------------------------------------------------------------


def llm_client() -> Optional[httpx.AsyncClient]:
    """The HTTP client model calls use; None lets the gateway build its own (the owner's proxy and the Portal's CA trust)."""
    return None


def connector_client() -> Optional[httpx.AsyncClient]:
    """The HTTP client connector requests use; None lets the proxy build its own."""
    return None


def image_client() -> Optional[httpx.AsyncClient]:
    """The HTTP client image calls use; None lets the call build its own (the proxy assigned to Image analysis, the Portal's CA trust)."""
    return None


def owner_config(db: Session, app: App) -> dict:
    """The effective settings of the app's owner: their model provider and connectors as their assistants get them."""
    owner = db.get(User, app.owner_user_id)
    if owner is None:
        return {}
    service = RuntimeProfileService(db)
    return service.effective_config_for(service.get_or_create_for_user(owner))


# --- who is calling ----------------------------------------------------------------


@dataclass
class SdkCall:
    """One authenticated call: the app, and plain copies of what outlives the request's session."""

    app: App
    app_id: str
    owner_user_id: int
    source: str
    visitor_user_id: Optional[int]


def _disabled_message(reason: Optional[str]) -> str:
    if reason == DISABLED_OWNER_INACTIVE:
        return "This app is disabled: its owner no longer has access to the Portal."
    return "This app has been disabled by an administrator."


def authenticate(request: Request, db: Session = Depends(get_db)) -> SdkCall:
    """The app behind the bearer token. Cookies are not looked at."""
    if not apps_settings.apps_enabled():
        raise SdkApiError("not_found", "Apps are not enabled on this Portal.", 404)
    scheme, _, presented = (request.headers.get("authorization") or "").partition(" ")
    app = app_tokens.find_app_by_token(db, presented.strip()) if scheme.lower() == "bearer" else None
    if app is None:
        raise SdkApiError("invalid_token", "A valid app token is required: send it as Authorization: Bearer <token>.", 401)
    enabled, reason = app_enabled(db, app)
    if not enabled:
        raise SdkApiError("app_disabled", _disabled_message(reason), 403)

    # Through the ingress (or any reverse proxy) a call carries one of these; from inside the cluster it does not.
    forwarded = request.headers.get("x-forwarded-for") is not None or request.headers.get("x-real-ip") is not None
    source = app_tokens.SOURCE_EXTERNAL if forwarded else app_tokens.SOURCE_CLUSTER
    wait = sdk_rate_limiter.check(app.id, apps_settings.sdk_rate_per_minute())
    app_tokens.note_token_use(db, app, source)
    if wait is not None:
        raise SdkApiError(
            "rate_limited",
            "This app is making too many SDK calls. Try again in %s seconds." % wait,
            429,
            retry_after=wait,
        )

    visitor = visitor_user_id(request.headers.get(VISITOR_HEADER), app.visitor_key, app.id)
    if visitor is not None and db.get(User, visitor) is None:
        visitor = None
    return SdkCall(app=app, app_id=app.id, owner_user_id=int(app.owner_user_id), source=source, visitor_user_id=visitor)


def _object(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise SdkApiError("invalid_request", "The request body must be a JSON object.", 400)
    return payload


# When a call's row could not be written at once (no pooled connection in
# time, a locked database), it is tried again after these pauses, in a thread
# of its own: the call happened, and its row is what the daily budget is
# counted from. The answer does not wait for these.
RECORD_RETRY_SECONDS = (1.0, 5.0, 30.0)


class _CallRecord:
    """Writes the ``app_executions`` row of one call, once, and gives back what the call was counted as while in flight."""

    def __init__(
        self,
        db: Session,
        call: SdkCall,
        kind: str,
        *,
        connector_type: Optional[str] = None,
        reservation: Optional[app_budget.Reservation] = None,
    ) -> None:
        # The request's session is given back before the provider or the
        # connector is asked (and is closed before a streamed answer is sent),
        # so the row is written through a session of its own.
        self._bind = db.get_bind()
        self._call = call
        self._kind = kind
        self._connector_type = connector_type
        self._reservation = reservation
        self._started = time.monotonic()
        self.model: Optional[str] = None
        self.done = False
        # Set by the body of a streamed answer once it runs: from then on its
        # own ending records the call (see _recorded_stream).
        self.stream_started = False

    def finish(
        self,
        status: str,
        *,
        error_code: Optional[str] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        credits: Optional[float] = None,
    ) -> None:
        """Write the row. Blocks on the database: callers on the event loop go through ``_finish``."""
        if self.done:
            return
        self.done = True
        fields = {
            "app_id": self._call.app_id,
            "owner_user_id": self._call.owner_user_id,
            "visitor_user_id": self._call.visitor_user_id,
            "kind": self._kind,
            "status": status,
            "model": self.model,
            "connector_type": self._connector_type,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "credits": credits,
            "error_code": error_code,
            "source": self._call.source,
            "duration_ms": (time.monotonic() - self._started) * 1000,
        }
        if self._write(fields):
            self._release()
            return
        # The call stays counted as in flight until its row is there, so the
        # budget always sees it in one place or the other.
        threading.Thread(target=self._write_later, args=(fields,), name="app-sdk-record", daemon=True).start()

    def _write(self, fields: dict) -> bool:
        try:
            with Session(bind=self._bind, autoflush=False) as db:
                return record_execution(db, **fields) is not None
        except Exception:  # noqa: BLE001 - bookkeeping never fails a call that happened
            logger.warning("app sdk: recording a call failed app_id=%s kind=%s", self._call.app_id, self._kind, exc_info=True)
            return False

    def _write_later(self, fields: dict) -> None:
        try:
            for pause in RECORD_RETRY_SECONDS:
                time.sleep(pause)
                if self._write(fields):
                    return
            logger.error(
                "app sdk: the row of a call could not be written and is lost app_id=%s kind=%s status=%s",
                self._call.app_id,
                self._kind,
                fields["status"],
            )
        finally:
            self._release()

    def _release(self) -> None:
        if self._reservation is not None:
            self._reservation.release()


async def _finish(record: _CallRecord, status: str, **fields: Any) -> None:
    """Write a call's row off the event loop, to the end even when the request is being cancelled.

    The write waits for the database, which the event loop must never do.
    A request that is being cancelled may wait for nothing unshielded, and
    its row is what the daily budget is counted from: hence the shield.
    """
    with anyio.CancelScope(shield=True):
        await run_in_threadpool(record.finish, status, **fields)


# --- 9.1 -----------------------------------------------------------------------------


def _enabled_connectors(app: App, config: dict) -> list[dict]:
    """The connectors the app may call and its owner has set up: ``{"type", "base_url", "write"}`` each.

    The proxied types only. Image analysis is turned on in the same list
    and is never one of these: it has no address an app could call.
    """
    write = set(app.connectors_write or [])
    found = []
    for kind in APP_CONNECTOR_TYPES:
        if kind not in (app.connectors_enabled or []):
            continue
        described = connector_proxy.describe(config, kind)
        if described is not None:
            found.append({"type": kind, "base_url": described["base_url"], "write": kind in write})
    return found


def _image_analysis_info(app: App, config: dict) -> dict:
    """``image_analysis`` of section 9.1: on when the owner turned it on for the app and their connector can run."""
    turned_on = has_capability(app.connectors_enabled, APP_IMAGE_ANALYSIS)
    model = image_analysis.configured_model(config) if turned_on else None
    return {"enabled": model is not None, "model": model, **image_analysis.limits()}


@router.get("/app")
def get_app_info(call: SdkCall = Depends(authenticate), db: Session = Depends(get_db)):
    app = call.app
    # A process that has not read the stored model catalog yet would offer the static list.
    model_catalog_service.current_catalog(db)
    config = owner_config(db, app)
    owner = db.get(User, app.owner_user_id)
    return {
        "id": app.id,
        "slug": app.slug,
        "name": app.name,
        "url": apps_settings.app_public_url(app.slug),
        "owner": user_payload(db, owner),
        "llm": {
            "enabled": bool(app.llm_enabled),
            "default_model": llm_gateway.default_model(config),
            "models": llm_gateway.available_models(config),
        },
        "connectors": [{"type": item["type"], "write": item["write"]} for item in _enabled_connectors(app, config)],
        "image_analysis": _image_analysis_info(app, config),
        "budget": app_budget.snapshot(db, app),
    }


# --- 9.2 -----------------------------------------------------------------------------


def _text_size(body: dict) -> int:
    """The characters of the request's own text (the messages and the system text)."""
    size = len(body["system"]) if isinstance(body.get("system"), str) else 0
    messages = body.get("messages")
    for item in messages if isinstance(messages, list) else []:
        if isinstance(item, dict) and isinstance(item.get("content"), str):
            size += len(item["content"])
    return size


def _recorded_tokens(usage: Optional[dict], request_chars: int, answer_chars: int) -> tuple[int, int]:
    """The tokens to count for a call: the provider's when it reported any, an estimate from the text otherwise.

    A stream that did not reach its end, and a provider that reports no usage
    on a stream, would otherwise cost the daily token budget nothing.
    """
    reported_in = int((usage or {}).get("input_tokens") or 0)
    reported_out = int((usage or {}).get("output_tokens") or 0)
    if reported_in or reported_out:
        return reported_in, reported_out
    if not answer_chars:
        return 0, 0
    return estimate_tokens(request_chars), estimate_tokens(answer_chars)


def _gateway_error(exc: LlmGatewayError) -> SdkApiError:
    return SdkApiError(exc.code, exc.message, exc.status, retry_after=exc.retry_after)


def _prepare_llm(db: Session, call: SdkCall) -> tuple[dict, dict]:
    """``(the owner's settings, the app's budget)``, read in one go off the event loop.

    The request's session is given back here. Nothing after this needs the
    database until the call's row is written, and a pooled connection held
    while a provider thinks is one the rest of the Portal cannot use: a few
    slow calls would leave none for anybody.
    """
    try:
        model_catalog_service.current_catalog(db)
        return owner_config(db, call.app), app_budget.snapshot(db, call.app)
    finally:
        db.close()


def _prepare_image(db: Session, call: SdkCall) -> tuple[dict, dict]:
    """``_prepare_llm`` for an image call: the same two things, without the model catalog it never asks.

    The app is read again by its id. The session it was first read through
    was given back before the body arrived (see ``images_inspect``), and
    the row with it.
    """
    try:
        app = db.get(App, call.app_id)
        if app is None:
            # Removed for good while its upload was arriving.
            raise SdkApiError("invalid_token", "A valid app token is required: send it as Authorization: Bearer <token>.", 401)
        return owner_config(db, app), app_budget.snapshot(db, app)
    finally:
        db.close()


def _prepare_connector(db: Session, app: App) -> dict:
    """The owner's settings for a connector request; the request's session is given back, as in ``_prepare_llm``."""
    try:
        return owner_config(db, app)
    finally:
        db.close()


async def _within_budget(
    db: Session,
    call: SdkCall,
    reservation: app_budget.Reservation,
    prepare: Callable[[Session, SdkCall], tuple[dict, dict]],
    *,
    spent: str,
    crowded: str,
) -> dict:
    """The owner's settings, for a call the daily budget lets through.

    The one gate of every call that is counted against the budget, a model
    call and an image call alike. ``reservation`` is what the call is
    counted as while it is under way, made before this is asked.
    ``budget_exceeded`` when the finished calls have used the day's credits
    or tokens (``spent`` says so, with which of the two), ``rate_limited``
    when the calls under way take up what is left (``crowded``, with the
    seconds to wait).
    """
    # The calls ahead of this one first, the rows second: one that ends
    # in between is then counted in both places rather than in neither.
    ahead = app_budget.in_flight.tokens(call.app_id, before=reservation)
    config, budget = await run_in_threadpool(prepare, db, call)
    used_up = app_budget.exceeded(budget)
    if used_up is not None:
        raise SdkApiError("budget_exceeded", spent % used_up, 429, retry_after=app_budget.seconds_to_reset())
    if app_budget.crowded(budget, ahead):
        # Not spent yet, and it may not come to that: those calls are
        # counted as the most they can use. Worth another try soon.
        raise SdkApiError(
            "rate_limited",
            crowded % app_budget.IN_FLIGHT_RETRY_SECONDS,
            429,
            retry_after=app_budget.IN_FLIGHT_RETRY_SECONDS,
        )
    return config


def _in_flight_tokens(request_chars: int, max_output_tokens: Any) -> int:
    """What a completion is counted as while it is under way: its text and the answer it asked for.

    Neither beyond what the gateway accepts: a request over those bounds is
    refused a moment later and must not hold the budget back meanwhile.
    """
    asked = max_output_tokens if isinstance(max_output_tokens, int) and not isinstance(max_output_tokens, bool) else None
    if asked is not None:
        asked = min(asked, llm_gateway.MAX_OUTPUT_TOKENS)
    return app_budget.reservation_tokens(estimate_tokens(min(request_chars, llm_gateway.MAX_INPUT_CHARS)), asked)


def _sse(event: dict) -> bytes:
    return ("data: " + json.dumps(event) + "\n\n").encode("utf-8")


@router.post("/llm/complete")
async def llm_complete(payload: Any = Body(default=None), call: SdkCall = Depends(authenticate), db: Session = Depends(get_db)):
    if not call.app.llm_enabled:
        raise SdkApiError("llm_disabled", "The owner has not turned on the model provider for this app.", 403)
    body = _object(payload)
    streaming = body.get("stream")
    if streaming is None:
        streaming = False
    if not isinstance(streaming, bool):
        raise SdkApiError("invalid_request", "stream must be true or false.", 400)

    request_chars = _text_size(body)
    # Counted as in flight from here on, before the budget is read: calls
    # that arrive together must see each other, or each of them reads the
    # same spent figure and all of them go through.
    reservation = app_budget.in_flight.reserve(call.app_id, _in_flight_tokens(request_chars, body.get("max_output_tokens")))
    try:
        config = await _within_budget(
            db,
            call,
            reservation,
            _prepare_llm,
            spent="This app has used its daily %s for the owner's model provider. The budget starts again at midnight UTC.",
            crowded="The model calls this app has under way take up what is left of its daily tokens. Try again in %s seconds.",
        )
        record = _CallRecord(db, call, app_budget.KIND_LLM, reservation=reservation)
    except BaseException:
        # Refused at a gate (or cancelled there): the call is not under way.
        reservation.release()
        raise

    # From here the record gives the reservation back, when the row is written.
    try:
        if not llm_gateway.is_configured(config):
            raise LlmGatewayError(
                "provider_auth_failed",
                "The owner's model provider is not connected. The owner has to set it up in the Model provider connector.",
            )
        record.model = llm_gateway.resolve_model(config, body.get("model"))
    except LlmGatewayError as exc:
        await _finish(record, STATUS_FAILED, error_code=exc.code)
        raise _gateway_error(exc) from None
    except Exception:
        await _finish(record, STATUS_FAILED, error_code=ERROR_INTERNAL)
        raise

    arguments = {
        "config": config,
        "model": record.model,
        "messages": body.get("messages"),
        "system": body.get("system"),
        "max_output_tokens": body.get("max_output_tokens"),
        "temperature": body.get("temperature"),
        "reasoning_effort": body.get("reasoning_effort"),
        "client": llm_client(),
    }

    if not streaming:
        try:
            result = await llm_gateway.complete(**arguments)
        except LlmGatewayError as exc:
            await _finish(record, STATUS_FAILED, error_code=exc.code)
            raise _gateway_error(exc) from None
        except Exception:
            await _finish(record, STATUS_FAILED, error_code=ERROR_INTERNAL)
            raise
        except BaseException:
            # The request was cancelled under us.
            await _finish(record, STATUS_CANCELLED, error_code=ERROR_CLIENT_CLOSED)
            raise
        usage = result.usage.as_dict()
        tokens_in, tokens_out = _recorded_tokens(usage, request_chars, len(result.text))
        await _finish(record, STATUS_SUCCEEDED, input_tokens=tokens_in, output_tokens=tokens_out, credits=usage["credits"])
        return {"text": result.text, "model": result.model, "finish_reason": result.finish_reason, "usage": usage}

    events = llm_gateway.stream(**arguments)
    # The first event is read before any status is sent: a completion that
    # fails before it produced anything still gets its real HTTP status.
    try:
        first = await events.__anext__()
    except LlmGatewayError as exc:
        await _finish(record, STATUS_FAILED, error_code=exc.code)
        raise _gateway_error(exc) from None
    except StopAsyncIteration:
        await _finish(record, STATUS_FAILED, error_code="provider_error")
        raise SdkApiError("provider_error", "The model provider ended the answer without saying how.", 502) from None
    except Exception:
        await _finish(record, STATUS_FAILED, error_code=ERROR_INTERNAL)
        raise
    except BaseException:
        await _finish(record, STATUS_CANCELLED, error_code=ERROR_CLIENT_CLOSED)
        raise
    return StreamingResponse(
        _recorded_stream(events, first, record, request_chars),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


def _recorded_stream(events: AsyncIterator[dict], first: dict, record: _CallRecord, request_chars: int) -> AsyncIterator[bytes]:
    """``_stream_body``, recorded even when nobody ever reads it.

    A generator that was never started has no ending to run. A request torn
    down between this point and the first piece of the answer would leave no
    row, and the call would stay counted as under way: so when such a body
    is thrown away unread, the call is recorded then.
    """
    body = _stream_body(events, first, record, request_chars)
    weakref.finalize(body, _record_unread_stream, record, first, request_chars).atexit = False
    return body


def _record_unread_stream(record: _CallRecord, first: dict, request_chars: int) -> None:
    if record.done or record.stream_started:
        # It ran: its own ending has recorded the call, or is about to.
        return
    answer_chars = len(first.get("text") or "") if first.get("type") == "delta" else 0
    tokens_in, tokens_out = _recorded_tokens(None, request_chars, answer_chars)
    # Wherever the garbage collector runs this, the write does not happen there.
    threading.Thread(
        target=record.finish,
        args=(STATUS_CANCELLED,),
        kwargs={"error_code": ERROR_CLIENT_CLOSED, "input_tokens": tokens_in, "output_tokens": tokens_out},
        name="app-sdk-record",
        daemon=True,
    ).start()


async def _stream_body(events: AsyncIterator[dict], first: dict, record: _CallRecord, request_chars: int) -> AsyncIterator[bytes]:
    """The events of a completion as server-sent events; the call is recorded when the stream ends, however it ends."""
    record.stream_started = True
    status, error_code = STATUS_CANCELLED, ERROR_CLIENT_CLOSED
    usage: Optional[dict] = None
    answer_chars = 0
    event = first
    try:
        while True:
            kind = event.get("type")
            if kind == "delta":
                answer_chars += len(event.get("text") or "")
            elif kind == "done":
                status, error_code = STATUS_SUCCEEDED, None
                usage = event.get("usage") if isinstance(event.get("usage"), dict) else None
            else:
                failure = event.get("error") if isinstance(event.get("error"), dict) else {}
                status, error_code = STATUS_FAILED, str(failure.get("code") or "provider_error")
            yield _sse(event)
            if kind != "delta":
                return
            try:
                event = await events.__anext__()
            except StopAsyncIteration:
                event = {"type": "error", "error": {"code": "provider_error", "message": "The model provider ended the answer without saying how."}}
            except LlmGatewayError as exc:
                event = {"type": "error", "error": exc.as_error()}
    finally:
        # Reached when the answer ended, when it failed and when the caller
        # went away (the task is then being cancelled: nothing unshielded may
        # be awaited here, so both waits below are shielded).
        tokens_in, tokens_out = _recorded_tokens(usage, request_chars, answer_chars)
        try:
            await _finish(
                record,
                status,
                error_code=error_code,
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                credits=(usage or {}).get("credits"),
            )
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    await events.aclose()
                except Exception:  # noqa: BLE001 - the provider's stream is gone either way
                    pass


# --- 9.3 -----------------------------------------------------------------------------


@router.get("/connectors")
def list_connectors(call: SdkCall = Depends(authenticate), db: Session = Depends(get_db)):
    return {"connectors": _enabled_connectors(call.app, owner_config(db, call.app))}


def _log_text(value: Any, limit: int) -> str:
    """A caller's text made safe for one log line: printable ASCII, no spaces, cut."""
    return _LOG_UNSAFE.sub("?", str(value or ""))[:limit] or "-"


def _log_path(path: Any) -> str:
    """The path of a connector request without its query string: a query may carry a token."""
    return _log_text(str(path or "").split("?", 1)[0].split("#", 1)[0], LOG_PATH_LIMIT)


def _raw_body(value: Any) -> Optional[bytes]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConnectorProxyError("invalid_request", "body_base64 must be base64 text.")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ConnectorProxyError("invalid_request", "body_base64 is not valid base64.") from None


@router.post("/connectors/{connector_type}/request")
async def connector_request(
    connector_type: str,
    payload: Any = Body(default=None),
    call: SdkCall = Depends(authenticate),
    db: Session = Depends(get_db),
):
    kind = connector_type.strip().lower()
    if kind not in connector_proxy.SUPPORTED:
        raise SdkApiError("invalid_request", "connector type must be one of: " + ", ".join(connector_proxy.SUPPORTED) + ".", 400)
    app = call.app
    if kind not in (app.connectors_enabled or []):
        raise SdkApiError("connector_disabled", "The owner has not turned on the %s connector for this app." % kind, 403)
    body = _object(payload)
    allow_write = kind in (app.connectors_write or [])

    # The owner's settings are read and the request's session is given back
    # before the other system is asked: ``app`` is not looked at after this.
    config = await run_in_threadpool(_prepare_connector, db, app)
    record = _CallRecord(db, call, app_budget.KIND_CONNECTOR, connector_type=kind)
    method, path = body.get("method"), body.get("path")
    outcome = "error"
    try:
        result = await connector_proxy.request(
            config=config,
            connector_type=kind,
            method=method,
            path=path,
            query=body.get("query"),
            headers=body.get("headers"),
            json_body=body.get("json"),
            body=_raw_body(body.get("body_base64")),
            allow_write=allow_write,
            client=connector_client(),
        )
    except ConnectorProxyError as exc:
        outcome = exc.code
        await _finish(record, STATUS_FAILED, error_code=exc.code)
        raise SdkApiError(exc.code, exc.message, exc.status) from None
    except Exception:
        outcome = ERROR_INTERNAL
        await _finish(record, STATUS_FAILED, error_code=ERROR_INTERNAL)
        raise
    except BaseException:
        outcome = ERROR_CLIENT_CLOSED
        await _finish(record, STATUS_CANCELLED, error_code=ERROR_CLIENT_CLOSED)
        raise
    else:
        outcome = str(result.get("status"))
        await _finish(record, STATUS_SUCCEEDED)
        return result
    finally:
        # The proxy keeps httpx's own request line out of the log (a query may
        # carry a token); this is the line that says the call happened.
        logger.info(
            "app sdk connector request app_id=%s type=%s method=%s path=%s status=%s",
            call.app_id,
            kind,
            _log_text(method, 10).upper(),
            _log_path(path),
            outcome,
        )


# --- 9.4 -----------------------------------------------------------------------------

BODY_TOO_LARGE = "The request body is larger than an image call can be: four images of 5 MiB each are about 28 MB of JSON."


async def _read_body(request: Request, limit: int) -> bytearray:
    """The request's body, when it holds at most ``limit`` bytes; ``invalid_request`` otherwise.

    The routes above let the framework read their body, whatever its size:
    theirs are small. This one is sent megabytes by design, so what it reads
    has a bound of its own. A body that declares more is refused unread;
    one that declares nothing, or nothing that can be read as a number, is
    refused at the piece that crosses the bound.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            too_large = int(declared) > limit
        except ValueError:
            too_large = False
        if too_large:
            raise SdkApiError("invalid_request", BODY_TOO_LARGE, 400)
    # One buffer that grows: megabytes are not held once as pieces and once joined.
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > limit:
                raise SdkApiError("invalid_request", BODY_TOO_LARGE, 400)
            body += chunk
    except ClientDisconnect:
        # The caller went away in the middle of its own upload: nobody reads
        # this answer, and it is no failure of the Portal's to write down.
        raise SdkApiError("invalid_request", "The request body did not arrive whole.", 400) from None
    return body


async def _image_request(request: Request) -> image_analysis.InspectRequest:
    """The body of an image call, read and checked whole; ``invalid_request`` for one section 9.4 does not allow."""
    raw = await _read_body(request, image_analysis.MAX_BODY_BYTES)
    try:
        # Off the event loop: megabytes of JSON and base64 take a moment to read.
        return await run_in_threadpool(image_analysis.parse_body, raw)
    except LlmGatewayError as exc:
        raise _gateway_error(exc) from None


def _image_tokens(usage: dict, input_estimate: int, answer_chars: int) -> tuple[int, int]:
    """The tokens to count for an answered image call: AI Platform's when it reported any.

    When it reported none, the input is what the call was counted as while
    it was under way (its text and its images) and the output is estimated
    from the answer, so that such a call does not cost the budget nothing.
    """
    reported_in = int(usage.get("input_tokens") or 0)
    reported_out = int(usage.get("output_tokens") or 0)
    if reported_in or reported_out:
        return reported_in, reported_out
    return input_estimate, estimate_tokens(answer_chars)


@router.post("/images/inspect")
async def images_inspect(request: Request, call: SdkCall = Depends(authenticate), db: Session = Depends(get_db)):
    # A switch of its own: whether the app may use the model provider (``llm_enabled``) is not asked.
    if not has_capability(call.app.connectors_enabled, APP_IMAGE_ANALYSIS):
        raise SdkApiError("connector_disabled", "The owner has not turned on image analysis for this app.", 403)
    # The body is megabytes and arrives at the caller's pace. The request's
    # session is given back before it is read, as it is before a provider is
    # waited for: ``call.app`` is not looked at after this.
    await run_in_threadpool(db.close)
    # Read and checked before anything is counted or sent: a request the
    # contract does not allow costs nothing, and leaves no row.
    asked = await _image_request(request)

    # Counted as a model call under way: a quarter of the characters of the
    # text, 1,500 tokens for each image, and the answer it may get.
    input_estimate = estimate_tokens(asked.text_chars) + image_analysis.IN_FLIGHT_TOKENS_PER_IMAGE * len(asked.images)
    reservation = app_budget.in_flight.reserve(
        call.app_id, app_budget.reservation_tokens(input_estimate, asked.max_output_tokens)
    )
    try:
        config = await _within_budget(
            db,
            call,
            reservation,
            _prepare_image,
            spent="This app has used its daily %s: image calls count against the same budget as model calls. "
            "The budget starts again at midnight UTC.",
            crowded="The calls this app has under way take up what is left of its daily tokens. Try again in %s seconds.",
        )
        # A model call of the app, told apart from the others by its connector.
        record = _CallRecord(db, call, app_budget.KIND_LLM, connector_type=APP_IMAGE_ANALYSIS, reservation=reservation)
    except BaseException:
        # Refused at a gate (or cancelled there): the call is not under way.
        reservation.release()
        raise

    # From here the record gives the reservation back, when the row is written.
    outcome = "error"
    try:
        # None for a connector that cannot run: the call below then says so.
        record.model = image_analysis.configured_model(config)
        result = await image_analysis.inspect(config=config, request=asked, client=image_client())
    except LlmGatewayError as exc:
        outcome = exc.code
        await _finish(record, STATUS_FAILED, error_code=exc.code)
        raise _gateway_error(exc) from None
    except Exception:
        outcome = ERROR_INTERNAL
        await _finish(record, STATUS_FAILED, error_code=ERROR_INTERNAL)
        raise
    except BaseException:
        # The request was cancelled under us.
        outcome = ERROR_CLIENT_CLOSED
        await _finish(record, STATUS_CANCELLED, error_code=ERROR_CLIENT_CLOSED)
        raise
    else:
        outcome = STATUS_SUCCEEDED
        usage = result.usage.as_dict()
        tokens_in, tokens_out = _image_tokens(usage, input_estimate, len(result.text))
        await _finish(record, STATUS_SUCCEEDED, input_tokens=tokens_in, output_tokens=tokens_out, credits=usage["credits"])
        return {"text": result.text, "model": result.model, "finish_reason": result.finish_reason, "usage": usage}
    finally:
        # How many images and how large, never what they hold.
        logger.info(
            "app sdk image call app_id=%s images=%s image_bytes=%s status=%s",
            call.app_id,
            len(asked.images),
            asked.image_bytes,
            outcome,
        )


# --- anything else below the prefix ---------------------------------------------------


@router.api_route("", methods=ALL_METHODS, include_in_schema=False)
@router.api_route("/{rest:path}", methods=ALL_METHODS, include_in_schema=False)
def unknown_endpoint(request: Request, call: SdkCall = Depends(authenticate)):
    raise SdkApiError("not_found", "The SDK API has no %s %s." % (request.method, _log_text(request.url.path, LOG_PATH_LIMIT)), 404)
