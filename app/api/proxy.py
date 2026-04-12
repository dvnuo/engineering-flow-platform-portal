import asyncio
import json
import logging
logger = logging.getLogger(__name__)
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
import websockets

from app.config import get_settings
from app.db import get_db
from app.db import SessionLocal
from app.deps import get_current_user
from app.log_context import bind_log_context, generate_span_id, generate_trace_id, get_log_context, reset_log_context
from app.repositories.agent_repo import AgentRepository
from app.repositories.user_repo import UserRepository
from app.services.auth_service import parse_session_token
from app.services.proxy_service import (
    ProxyService,
    build_portal_execution_headers,
    build_portal_identity_headers,
    build_runtime_trace_headers,
)
from app.services.runtime_execution_context_service import RuntimeExecutionContextService
from app.redaction import sanitize_exception_message

router = APIRouter(tags=["proxy"])
proxy_service = ProxyService()
runtime_execution_context_service = RuntimeExecutionContextService()
settings = get_settings()


def _can_access(agent, user) -> bool:
    return user.role == "admin" or agent.owner_user_id == user.id or agent.visibility == "public"


def _can_write(agent, user) -> bool:
    return user.role == "admin" or agent.owner_user_id == user.id


def _requires_write_access(method: str, subpath: str) -> bool:
    normalized = (subpath or "").strip("/").lower()
    if normalized.startswith("api/server-files"):
        return True
    return (method.upper(), normalized) in {
        ("POST", "api/config/save"),
    }


def _filter_proxy_query_items(query_items):
    return [(k, v) for k, v in query_items if k.lower() != "token"]




def _is_direct_chat_execution_path(method: str, subpath: str) -> bool:
    normalized = (subpath or "").strip("/").lower()
    return method.upper() == "POST" and normalized in {"api/chat", "api/chat/stream"}


def _content_type_is_json(content_type: str | None) -> bool:
    return bool(content_type and "application/json" in content_type.lower())


def _select_streaming_response_headers(upstream_headers) -> dict[str, str]:
    allowed = {"content-type", "cache-control", "x-accel-buffering"}
    selected = {}
    for key in allowed:
        value = upstream_headers.get(key)
        if value:
            selected[key] = value
    return selected


def _enrich_chat_payload_with_runtime_metadata(payload: dict, runtime_metadata: dict, user) -> dict:
    enriched = dict(payload)
    _ = user
    enriched.pop("metadata", None)
    enriched.pop("capability_context", None)
    enriched.pop("policy_context", None)
    enriched.pop("portal_user_id", None)
    enriched.pop("portal_user_name", None)

    enriched["metadata"] = runtime_metadata
    return enriched
@router.api_route("/a/{agent_id}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@router.api_route("/a/{agent_id}/{subpath:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_agent(
    agent_id: str,
    request: Request,
    subpath: str = "",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not _can_access(agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if _requires_write_access(request.method, subpath) and not _can_write(agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if agent.status != "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Agent is not running")

    try:
        forward_headers = {}
        content_type = request.headers.get("content-type")
        if content_type:
            forward_headers["content-type"] = content_type

        request_body = (await request.body()) or None
        is_direct_chat_execution = _is_direct_chat_execution_path(request.method, subpath)
        if is_direct_chat_execution:
            extra_headers = build_portal_execution_headers(user)
        else:
            extra_headers = build_portal_identity_headers(user)

        if is_direct_chat_execution and request_body:
            if not _content_type_is_json(content_type):
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail="Direct chat execution requires application/json content-type",
                )
            try:
                parsed_payload = json.loads(request_body.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload") from exc
            if not isinstance(parsed_payload, dict):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="JSON payload must be an object")
            runtime_metadata = runtime_execution_context_service.build_runtime_metadata(db, agent)
            parsed_payload = _enrich_chat_payload_with_runtime_metadata(parsed_payload, runtime_metadata, user)
            request_body = json.dumps(parsed_payload).encode("utf-8")

        normalized_subpath = (subpath or "").strip("/").lower()
        if request.method.upper() == "POST" and normalized_subpath == "api/chat/stream":
            try:
                base = proxy_service.build_agent_base_url(agent).rstrip("/")
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

            upstream_url = f"{base}/api/chat/stream"
            outbound_headers = proxy_service._build_outbound_headers(forward_headers, extra_headers)
            client = httpx.AsyncClient(timeout=None)
            try:
                upstream_response = await client.stream(
                    method="POST",
                    url=upstream_url,
                    params=_filter_proxy_query_items(request.query_params.multi_items()),
                    content=request_body,
                    headers=outbound_headers,
                ).__aenter__()
            except Exception:
                await client.aclose()
                raise

            async def _close_stream_resources() -> None:
                await upstream_response.aclose()
                await client.aclose()

            stream_headers = _select_streaming_response_headers(upstream_response.headers)
            media_type = upstream_response.headers.get("content-type")
            return StreamingResponse(
                upstream_response.aiter_raw(),
                status_code=upstream_response.status_code,
                media_type=media_type,
                headers=stream_headers,
                background=BackgroundTask(_close_stream_resources),
            )

        status_code, content, content_type = await proxy_service.forward(
            agent=agent,
            method=request.method,
            subpath=subpath,
            query_items=_filter_proxy_query_items(request.query_params.multi_items()),
            body=request_body,
            headers=forward_headers,
            extra_headers=extra_headers,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Proxy error agent_id=%s method=%s subpath=%s", agent_id, request.method, subpath)
        safe_error = sanitize_exception_message(exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Proxy upstream failure: {safe_error}") from exc

    return Response(status_code=status_code, content=content, media_type=content_type)


@router.websocket("/a/{agent_id}/api/events")
async def proxy_agent_events(agent_id: str, websocket: WebSocket):
    trace_id = (websocket.headers.get("X-Trace-Id") or websocket.headers.get("X-Request-Id") or "").strip()
    if not trace_id:
        trace_id = generate_trace_id()
    context_token = bind_log_context(
        trace_id=trace_id,
        span_id=generate_span_id(),
        parent_span_id="-",
        portal_dispatch_id="-",
        portal_task_id="-",
        agent_id=agent_id,
        path=f"/a/{agent_id}/api/events",
    )
    try:
        db = SessionLocal()
        try:
            # Try cookie first, then query param
            token = websocket.cookies.get(settings.session_cookie_name)
            if not token:
                # Check query parameter
                token = websocket.query_params.get("token")

            if not token:
                await websocket.close(code=4401, reason="Not authenticated")
                return

            user_id = parse_session_token(token)
            if not user_id:
                await websocket.close(code=4401, reason="Invalid session")
                return

            user = UserRepository(db).get_by_id(user_id)
            if not user or not user.is_active:
                await websocket.close(code=4401, reason="Inactive user")
                return

            agent = AgentRepository(db).get_by_id(agent_id)
            if not agent:
                await websocket.close(code=4404, reason="Agent not found")
                return
            if not _can_access(agent, user):
                await websocket.close(code=4403, reason="Forbidden")
                return
            if agent.status != "running":
                await websocket.close(code=4409, reason="Agent is not running")
                return
        finally:
            db.close()

        try:
            base = proxy_service.build_agent_base_url(agent).rstrip("/")
        except ValueError:
            await websocket.close(code=1011, reason="Runtime URL unavailable")
            return

        await websocket.accept()

        if base.startswith("https://"):
            ws_base = "wss://" + base[len("https://") :]
        elif base.startswith("http://"):
            ws_base = "ws://" + base[len("http://") :]
        else:
            ws_base = base

        upstream_url = f"{ws_base}/api/events"
        query_items = _filter_proxy_query_items(websocket.query_params.multi_items())
        if query_items:
            upstream_url = f"{upstream_url}?{urlencode(query_items)}"

        try:
            async with websockets.connect(
                upstream_url,
                additional_headers=build_runtime_trace_headers(get_log_context()),
            ) as upstream:
                async def client_to_upstream():
                    while True:
                        try:
                            message = await websocket.receive()
                        except WebSocketDisconnect:
                            break

                        msg_type = message.get("type")
                        if msg_type == "websocket.disconnect":
                            break

                        if message.get("text") is not None:
                            await upstream.send(message["text"])
                        elif message.get("bytes") is not None:
                            await upstream.send(message["bytes"])

                async def upstream_to_client():
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)

                upstream_task = asyncio.create_task(upstream_to_client())
                client_task = asyncio.create_task(client_to_upstream())
                done, pending = await asyncio.wait(
                    {upstream_task, client_task}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                for task in done:
                    await task
        except Exception:
            await websocket.close(code=1011)
    finally:
        reset_log_context(context_token)
