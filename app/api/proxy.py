import asyncio
import json
import logging
logger = logging.getLogger(__name__)
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session
import websockets

from app.config import get_settings
from app.db import get_db
from app.db import SessionLocal
from app.deps import get_current_user
from app.repositories.agent_repo import AgentRepository
from app.repositories.user_repo import UserRepository
from app.services.auth_service import parse_session_token
from app.services.proxy_service import ProxyService, build_portal_identity_fields, build_portal_identity_headers
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
    return (method.upper(), normalized) in {
        ("GET", "api/ssh/public-key"),
        ("POST", "api/ssh/generate"),
        ("POST", "api/config/save"),
    }


def _filter_proxy_query_items(query_items):
    return [(k, v) for k, v in query_items if k.lower() != "token"]




def _is_direct_chat_execution_path(method: str, subpath: str) -> bool:
    normalized = (subpath or "").strip("/").lower()
    return method.upper() == "POST" and normalized in {"api/chat", "api/chat/stream"}


def _build_direct_chat_headers(user) -> dict[str, str]:
    headers = build_portal_identity_headers(user)
    if settings.portal_internal_api_key:
        headers["X-Portal-Internal-Api-Key"] = settings.portal_internal_api_key
    return headers


def _enrich_chat_payload_with_runtime_metadata(payload: dict, runtime_metadata: dict, user) -> dict:
    enriched = dict(payload)
    enriched.pop("metadata", None)
    enriched.pop("capability_context", None)
    enriched.pop("policy_context", None)

    identity = build_portal_identity_fields(user)
    if identity.get("user_id"):
        enriched["portal_user_id"] = identity["user_id"]
    if identity.get("user_name"):
        enriched["portal_user_name"] = identity["user_name"]

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
        extra_headers = _build_direct_chat_headers(user) if is_direct_chat_execution else build_portal_identity_headers(user)

        if is_direct_chat_execution and content_type and "application/json" in content_type.lower() and request_body:
            try:
                parsed_payload = json.loads(request_body.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                parsed_payload = None
            if isinstance(parsed_payload, dict):
                runtime_metadata = runtime_execution_context_service.build_runtime_metadata(db, agent)
                parsed_payload = _enrich_chat_payload_with_runtime_metadata(parsed_payload, runtime_metadata, user)
                request_body = json.dumps(parsed_payload).encode("utf-8")

        status_code, content, content_type = await proxy_service.forward(
            agent=agent,
            method=request.method,
            subpath=subpath,
            query_items=_filter_proxy_query_items(request.query_params.multi_items()),
            body=request_body,
            headers=forward_headers,
            extra_headers=extra_headers,
        )
    except Exception as exc:
        logger.exception("Proxy error agent_id=%s method=%s subpath=%s", agent_id, request.method, subpath)
        safe_error = sanitize_exception_message(exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Proxy upstream failure: {safe_error}") from exc

    return Response(status_code=status_code, content=content, media_type=content_type)


@router.websocket("/a/{agent_id}/api/events")
async def proxy_agent_events(agent_id: str, websocket: WebSocket):
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

    await websocket.accept()

    base = proxy_service.build_agent_base_url(agent).rstrip("/")
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
        async with websockets.connect(upstream_url) as upstream:
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
