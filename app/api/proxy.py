import asyncio
import json
import logging
from pathlib import PurePosixPath
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
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.repositories.user_repo import UserRepository
from app.services.auth_service import parse_session_token
from app.services.proxy_service import (
    ProxyService,
    build_portal_agent_identity_headers,
    build_runtime_trace_headers,
)
from app.services.runtime_execution_context_service import RuntimeExecutionContextService
from app.services.runtime_profile_service import RuntimeProfileService
from app.schemas.runtime_profile import parse_runtime_profile_config_json
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
    method_upper = method.upper()
    if normalized.startswith("api/server-files"):
        return True
    if normalized == "api/sessions" or normalized.startswith("api/sessions/"):
        return method_upper not in {"GET", "HEAD", "OPTIONS"}
    return False


def _is_removed_legacy_ssh_path(subpath: str) -> bool:
    normalized = (subpath or "").strip("/").lower()
    return normalized == "api/ssh" or normalized.startswith("api/ssh/")


def _is_removed_portal_managed_config_path(subpath: str) -> bool:
    normalized = (subpath or "").strip("/").lower()
    return normalized in {"api/config", "api/config/save"}


def _is_control_plane_only_runtime_path(subpath: str) -> bool:
    normalized = (subpath or "").strip("/").lower()
    return normalized == "api/internal" or normalized.startswith("api/internal/")


def _filter_proxy_query_items(query_items):
    return [(k, v) for k, v in query_items if k.lower() != "token"]


def _safe_download_filename(name: str, fallback: str = "download") -> str:
    candidate = str(name or "")
    candidate = candidate.replace("\r", "").replace("\n", "").replace("\t", "").replace("\x00", "")
    candidate = candidate.replace("/", "").replace("\\", "").replace('"', "'").strip()
    return candidate or fallback


def _attachment_content_disposition(filename: str) -> str:
    safe_filename = _safe_download_filename(filename, fallback="download")
    return f'attachment; filename="{safe_filename}"'


def _is_zip_content_type(content_type: str | None) -> bool:
    if not content_type:
        return False
    return "application/zip" in content_type.lower()


def _server_files_download_fallback_filename(query_items, content_type: str) -> str:
    paths = [value for key, value in query_items if key == "paths" and value]
    if not paths:
        single_path = next((value for key, value in query_items if key == "path" and value), "")
        if single_path:
            paths = [single_path]

    if len(paths) > 1:
        return "server-files-selection.zip"

    selected_path = paths[0] if paths else ""
    basename = PurePosixPath(selected_path).name if selected_path else ""

    if _is_zip_content_type(content_type):
        if basename:
            if not basename.lower().endswith(".zip"):
                basename = f"{basename}.zip"
        else:
            basename = "server-files.zip"
    elif not basename:
        basename = "server-files"

    return _safe_download_filename(basename, fallback="server-files.zip" if _is_zip_content_type(content_type) else "server-files")




def _is_direct_chat_execution_path(method: str, subpath: str) -> bool:
    normalized = (subpath or "").strip("/").lower()
    return method.upper() == "POST" and normalized in {"api/chat", "api/chat/stream"}


def _is_streaming_runtime_path(method: str, subpath: str) -> bool:
    normalized = (subpath or "").strip("/").lower()
    method_upper = method.upper()
    return (method_upper == "POST" and normalized == "api/chat/stream") or (
        method_upper == "GET" and normalized in {"api/events", "api/events/stream"}
    )


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


def _normalize_and_validate_model_override_for_agent(
    payload: dict,
    *,
    agent,
    db: Session,
) -> dict:
    normalized = dict(payload)
    if "model_override" not in normalized:
        return normalized

    override = normalized.get("model_override")
    if override is None:
        normalized.pop("model_override", None)
        return normalized
    if not isinstance(override, str):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="model_override must be a string")

    trimmed = override.strip()
    if not trimmed:
        normalized.pop("model_override", None)
        return normalized

    runtime_profile_id = str(getattr(agent, "runtime_profile_id", "") or "").strip()
    allowed = False
    if runtime_profile_id:
        profile = RuntimeProfileRepository(db).get_by_id(runtime_profile_id)
        if profile:
            parsed = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
            llm = parsed.get("llm") if isinstance(parsed, dict) else {}
            if not isinstance(llm, dict):
                llm = {}
            provider = RuntimeProfileService.normalize_managed_llm_provider(llm.get("provider"))
            allowed = RuntimeProfileService.is_managed_model_allowed(provider, trimmed)

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="model_override is not allowed for the agent's current runtime profile provider",
        )

    normalized["model_override"] = trimmed
    return normalized


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
    if _is_removed_legacy_ssh_path(subpath):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Legacy SSH runtime endpoints have been removed",
        )
    if _is_removed_portal_managed_config_path(subpath):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Runtime config endpoints are no longer exposed via Portal proxy. Use Runtime Profiles in Portal instead.",
        )
    if _is_control_plane_only_runtime_path(subpath):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Runtime internal endpoints are not exposed via the user-facing Portal proxy.",
        )
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
        extra_headers = build_portal_agent_identity_headers(user, agent)

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
            parsed_payload = _normalize_and_validate_model_override_for_agent(
                parsed_payload,
                agent=agent,
                db=db,
            )
            runtime_metadata = runtime_execution_context_service.build_runtime_metadata(db, agent)
            parsed_payload = _enrich_chat_payload_with_runtime_metadata(parsed_payload, runtime_metadata, user)
            request_body = json.dumps(parsed_payload).encode("utf-8")

        if _is_streaming_runtime_path(request.method, subpath):
            try:
                base = proxy_service.build_agent_base_url(agent).rstrip("/")
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

            path = f"/{subpath.strip('/')}" if subpath else "/"
            upstream_url = f"{base}{path}"
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

        filtered_query_items = _filter_proxy_query_items(request.query_params.multi_items())
        normalized_subpath = (subpath or "").strip("/").lower()
        if request.method.upper() == "GET" and normalized_subpath == "api/server-files/download":
            status_code, content, content_type, response_headers = await proxy_service.forward(
                agent=agent,
                method=request.method,
                subpath=subpath,
                query_items=filtered_query_items,
                body=request_body,
                headers=forward_headers,
                extra_headers=extra_headers,
                return_response_headers=True,
            )
            if status_code < 400 and "Content-Disposition" not in response_headers:
                fallback_filename = _server_files_download_fallback_filename(filtered_query_items, content_type)
                response_headers["Content-Disposition"] = _attachment_content_disposition(fallback_filename)
            return Response(status_code=status_code, content=content, media_type=content_type, headers=response_headers)

        status_code, content, content_type = await proxy_service.forward(
            agent=agent,
            method=request.method,
            subpath=subpath,
            query_items=filtered_query_items,
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
                additional_headers={
                    **build_runtime_trace_headers(get_log_context()),
                    **build_portal_agent_identity_headers(user, agent),
                },
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
