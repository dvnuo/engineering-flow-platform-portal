"""The SDK API (docs/CONTRACTS.md, section 9): ``/api/app-sdk/v1``.

What an app's own backend asks the Portal for, with the app's token and
nothing else: no cookie is ever read here and no CORS header is ever sent, so
a page in a browser cannot call it. Reachable from the cluster and through
the Portal's public address.

- ``GET  /app``                        the app, and what it may use.
- ``POST /llm/complete``               a text completion on the owner's model provider.
- ``GET  /connectors``                 the owner's connectors the app may call.
- ``POST /connectors/{type}/request``  one HTTP request to one of them.

Every failure, a malformed body and an unknown path below the prefix
included, answers ``{"error": {"code", "message"}}``; every route answers 404
while ``APPS_ENABLED`` is off. Each call is checked in this order: the token
(``invalid_token``), the app (``app_disabled``), the app's rate
(``rate_limited``), then what the route itself asks for.

A call the app was allowed to make leaves one ``app_executions`` row, however
it ended (app/services/apps/executions.py); a call refused at one of the gates
(the token, the rate, a capability that is off, the daily budget) leaves none.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import logging
import re
import time
from typing import Any, AsyncIterator, Callable, Optional

import anyio
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
import httpx
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.app import App
from app.models.user import User
from app.services import model_catalog_service
from app.services.app_sdk import connector_proxy, llm_gateway
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
from app.services.apps.names import APP_CONNECTOR_TYPES, DISABLED_OWNER_INACTIVE
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


class _CallRecord:
    """Writes the ``app_executions`` row of one call, once."""

    def __init__(self, db: Session, call: SdkCall, kind: str, *, connector_type: Optional[str] = None) -> None:
        # The request's session is closed before a streamed answer has been
        # sent, so the row of a stream is written through a session of its own.
        self._bind = db.get_bind()
        self._call = call
        self._kind = kind
        self._connector_type = connector_type
        self._started = time.monotonic()
        self.model: Optional[str] = None
        self.done = False

    def finish(
        self,
        status: str,
        *,
        error_code: Optional[str] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        credits: Optional[float] = None,
    ) -> None:
        if self.done:
            return
        self.done = True
        with Session(bind=self._bind, autoflush=False) as db:
            record_execution(
                db,
                app_id=self._call.app_id,
                owner_user_id=self._call.owner_user_id,
                visitor_user_id=self._call.visitor_user_id,
                kind=self._kind,
                status=status,
                model=self.model,
                connector_type=self._connector_type,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                credits=credits,
                error_code=error_code,
                source=self._call.source,
                duration_ms=(time.monotonic() - self._started) * 1000,
            )


# --- 9.1 -----------------------------------------------------------------------------


def _enabled_connectors(app: App, config: dict) -> list[dict]:
    """The connectors the app may call and its owner has set up: ``{"type", "base_url", "write"}`` each."""
    write = set(app.connectors_write or [])
    found = []
    for kind in APP_CONNECTOR_TYPES:
        if kind not in (app.connectors_enabled or []):
            continue
        described = connector_proxy.describe(config, kind)
        if described is not None:
            found.append({"type": kind, "base_url": described["base_url"], "write": kind in write})
    return found


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
    """``(the owner's settings, the app's budget)``, read in one go off the event loop."""
    model_catalog_service.current_catalog(db)
    return owner_config(db, call.app), app_budget.snapshot(db, call.app)


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

    config, budget = await run_in_threadpool(_prepare_llm, db, call)
    spent = app_budget.exceeded(budget)
    if spent is not None:
        raise SdkApiError(
            "budget_exceeded",
            "This app has used its daily %s for the owner's model provider. The budget starts again at midnight UTC." % spent,
            429,
            retry_after=app_budget.seconds_to_reset(),
        )

    record = _CallRecord(db, call, app_budget.KIND_LLM)
    request_chars = _text_size(body)
    try:
        if not llm_gateway.is_configured(config):
            raise LlmGatewayError(
                "provider_auth_failed",
                "The owner's model provider is not connected. The owner has to set it up in the Model provider connector.",
            )
        record.model = llm_gateway.resolve_model(config, body.get("model"))
    except LlmGatewayError as exc:
        await run_in_threadpool(record.finish, STATUS_FAILED, error_code=exc.code)
        raise _gateway_error(exc) from None

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
            await run_in_threadpool(record.finish, STATUS_FAILED, error_code=exc.code)
            raise _gateway_error(exc) from None
        except Exception:
            await run_in_threadpool(record.finish, STATUS_FAILED, error_code=ERROR_INTERNAL)
            raise
        except BaseException:
            # The request was cancelled under us: nothing more may be awaited here.
            record.finish(STATUS_CANCELLED, error_code=ERROR_CLIENT_CLOSED)
            raise
        usage = result.usage.as_dict()
        tokens_in, tokens_out = _recorded_tokens(usage, request_chars, len(result.text))
        await run_in_threadpool(
            record.finish, STATUS_SUCCEEDED, input_tokens=tokens_in, output_tokens=tokens_out, credits=usage["credits"]
        )
        return {"text": result.text, "model": result.model, "finish_reason": result.finish_reason, "usage": usage}

    events = llm_gateway.stream(**arguments)
    # The first event is read before any status is sent: a completion that
    # fails before it produced anything still gets its real HTTP status.
    try:
        first = await events.__anext__()
    except LlmGatewayError as exc:
        await run_in_threadpool(record.finish, STATUS_FAILED, error_code=exc.code)
        raise _gateway_error(exc) from None
    except StopAsyncIteration:
        await run_in_threadpool(record.finish, STATUS_FAILED, error_code="provider_error")
        raise SdkApiError("provider_error", "The model provider ended the answer without saying how.", 502) from None
    except Exception:
        await run_in_threadpool(record.finish, STATUS_FAILED, error_code=ERROR_INTERNAL)
        raise
    except BaseException:
        record.finish(STATUS_CANCELLED, error_code=ERROR_CLIENT_CLOSED)
        raise
    return StreamingResponse(
        _stream_body(events, first, record, request_chars),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def _stream_body(events: AsyncIterator[dict], first: dict, record: _CallRecord, request_chars: int) -> AsyncIterator[bytes]:
    """The events of a completion as server-sent events; the call is recorded when the stream ends, however it ends."""
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
        # be awaited here, so the row is written in place).
        tokens_in, tokens_out = _recorded_tokens(usage, request_chars, answer_chars)
        record.finish(
            status,
            error_code=error_code,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            credits=(usage or {}).get("credits"),
        )
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

    config = await run_in_threadpool(owner_config, db, app)
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
        await run_in_threadpool(record.finish, STATUS_FAILED, error_code=exc.code)
        raise SdkApiError(exc.code, exc.message, exc.status) from None
    except Exception:
        outcome = ERROR_INTERNAL
        await run_in_threadpool(record.finish, STATUS_FAILED, error_code=ERROR_INTERNAL)
        raise
    except BaseException:
        outcome = ERROR_CLIENT_CLOSED
        record.finish(STATUS_CANCELLED, error_code=ERROR_CLIENT_CLOSED)
        raise
    else:
        outcome = str(result.get("status"))
        await run_in_threadpool(record.finish, STATUS_SUCCEEDED)
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


# --- anything else below the prefix ---------------------------------------------------


@router.api_route("", methods=ALL_METHODS, include_in_schema=False)
@router.api_route("/{rest:path}", methods=ALL_METHODS, include_in_schema=False)
def unknown_endpoint(request: Request, call: SdkCall = Depends(authenticate)):
    raise SdkApiError("not_found", "The SDK API has no %s %s." % (request.method, _log_text(request.url.path, LOG_PATH_LIMIT)), 404)
