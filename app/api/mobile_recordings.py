"""Recording panel API, the Inspector's WebDriver proxy, and the hosted Inspector.

See app/services/mobile_recording_service.py for the flow. The proxy lives at
/app/mobile/wd/{recording id}/{token}/..., which is the "remote path" the
hosted Inspector is opened with; the Inspector itself is served at
/inspector/ because its web build hard-codes that prefix.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models.mobile_recording import MobileRecording
from app.repositories.agent_repo import AgentRepository
from app.services.app_package_service import AppPackageError, browserstack_account_for_user
from app.services.mobile_recording_service import (
    HANDSHAKE_PATH,
    RECORDINGS_DIR,
    MobileRecordingService,
    RecordingError,
    appium_inspector_dir,
    element_attributes,
    is_secret_element,
    parse_handshake,
    source_cache,
    summarize_log,
    synthetic_capabilities,
    token_matches,
    valid_segment_name,
)
from app.services.outbound_http import browserstack_client_kwargs
from app.services.proxy_service import ProxyService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["mobile-recordings"])
proxy_service = ProxyService()

_PROXY_TIMEOUT_SECONDS = 180.0
_SESSION_PATH = re.compile(r"^session/([^/]+)(/.*)?$")
_FIND_PATH = re.compile(r"^(?:/element/[^/]+)?/(element|elements)$")
_ELEMENT_ACTION_PATH = re.compile(r"^/element/([^/]+)/(click|clear|value)$")
_LOGGED_ACTION_PATHS = {"/actions", "/back", "/execute/sync", "/execute", "/context"}
_W3C_ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf"


class OpenRecordingRequest(BaseModel):
    agent_id: str
    segment: Optional[str] = None


class SegmentDoneRequest(BaseModel):
    segment: Optional[str] = None
    next_segment: Optional[str] = None


def _http(exc: RecordingError | AppPackageError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _owned_agent(db: Session, user, agent_id: str):
    agent = AgentRepository(db).get_by_id(agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant not found")
    # The session is in the owner's BrowserStack account and the proxy signs
    # in with the viewer's own connector, so only the owner can record.
    if agent.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the assistant's owner can record on it")
    return agent


def _recording_view(service: MobileRecordingService, row: MobileRecording) -> dict[str, Any]:
    return {
        "id": row.id,
        "agent_id": row.agent_id,
        "session_id": row.session_id,
        "platform": row.platform,
        "device": row.device,
        "segment": row.segment,
        "status": row.status,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "last_event_at": row.last_event_at.isoformat() + "Z" if row.last_event_at else None,
        "segment_summary": summarize_log(service.segment_log(row, row.segment)),
    }


def _inspector_url(request: Request, row: MobileRecording, token: str) -> str:
    base = (get_settings().base_uri or str(request.base_url)).rstrip("/")
    parsed = urlparse(base)
    ssl = parsed.scheme == "https"
    state = {
        "serverType": "remote",
        "server": {
            "remote": {
                "hostname": parsed.hostname,
                "port": parsed.port or (443 if ssl else 80),
                "path": f"/app/mobile/wd/{row.id}/{token}",
                "ssl": ssl,
            }
        },
        "attachSessId": row.session_id,
    }
    # The Inspector reads ?state= into its session builder and, with
    # autoStart=1 and a session id, attaches straight away.
    return f"/inspector/?state={quote(json.dumps(state, separators=(',', ':')))}&autoStart=1"


@router.get("/api/mobile-recordings/status")
def recording_status(agent_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    agent = _owned_agent(db, user, agent_id)
    service = MobileRecordingService(db)
    row = service.active_for(user, agent.id)
    result: dict[str, Any] = {
        "inspector_available": appium_inspector_dir() is not None,
        "handshake_path": HANDSHAKE_PATH,
        "recordings_dir": RECORDINGS_DIR,
        "recording": _recording_view(service, row) if row else None,
    }
    try:
        result["hub_url"] = browserstack_account_for_user(db, user).hub_url
    except AppPackageError as exc:
        result["account_error"] = exc.message
    return result


@router.post("/api/mobile-recordings", status_code=status.HTTP_201_CREATED)
async def open_recording(payload: OpenRecordingRequest, request: Request, user=Depends(get_current_user), db: Session = Depends(get_db)):
    agent = _owned_agent(db, user, payload.agent_id)
    if (agent.status or "").lower() != "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Start the assistant first")
    if payload.segment and not valid_segment_name(payload.segment):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Segment names use letters, digits, dot, dash and underscore")
    try:
        account = browserstack_account_for_user(db, user)
    except AppPackageError as exc:
        raise _http(exc) from exc
    status_code, body, _content_type = await proxy_service.forward(
        agent=agent,
        method="GET",
        subpath="api/server-files/content",
        query_items=[("path", HANDSHAKE_PATH)],
        body=None,
        headers={},
    )
    if status_code == 404:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The assistant has not published a recording session yet. Start one from the Recording panel first.",
        )
    if status_code >= 400:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not read the recording session from the assistant (HTTP {status_code})")
    try:
        handshake = parse_handshake(body)
    except RecordingError as exc:
        raise _http(exc) from exc
    service = MobileRecordingService(db)
    # The hub is always the member's configured BrowserStack hub, never a URL
    # from the workspace file.
    row, token = service.open(user, agent, handshake, account.hub_url, payload.segment)
    view = _recording_view(service, row)
    view["token"] = token
    view["inspector_url"] = _inspector_url(request, row, token) if appium_inspector_dir() else None
    view["hold_deadline"] = handshake.get("hold_deadline")
    return view


@router.post("/api/mobile-recordings/{recording_id}/segments")
async def finish_segment(recording_id: str, payload: SegmentDoneRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = MobileRecordingService(db)
    row = service.get_for_user(user, recording_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")
    if row.status != "active":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This recording is closed")
    if payload.segment and not valid_segment_name(payload.segment):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Segment names use letters, digits, dot, dash and underscore")
    name = valid_segment_name(payload.segment) or row.segment
    doc = service.segment_log(row, name)
    summary = summarize_log(doc)
    if summary["actions"] == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nothing was recorded in this segment yet. Tap and type in the Inspector first.",
        )
    agent = AgentRepository(db).get_by_id(row.agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant not found")
    file_name = f"{name}.wdlog.json"
    status_code, _body, _content_type = await proxy_service.forward_multipart(
        agent=agent,
        method="POST",
        subpath="api/server-files/upload",
        query_items=[],
        files={"file": (file_name, json.dumps(doc, indent=2).encode("utf-8"), "application/json")},
        data={"path": RECORDINGS_DIR},
    )
    if status_code >= 400:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not write the segment into the assistant's workspace (HTTP {status_code})")
    next_name = service.next_segment(row, payload.next_segment)
    return {"segment": name, "path": f"{RECORDINGS_DIR}/{file_name}", **summary, "next_segment": next_name}


@router.post("/api/mobile-recordings/{recording_id}/close")
def close_recording(recording_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = MobileRecordingService(db)
    row = service.get_for_user(user, recording_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")
    if row.status == "active":
        service.close(row)
    return {"ok": True}


# ----- WebDriver proxy ---------------------------------------------------------


def _wd_error(status_code: int, error: str, message: str) -> JSONResponse:
    return JSONResponse({"value": {"error": error, "message": message, "stacktrace": ""}}, status_code=status_code)


def _element_id(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        found = value.get(_W3C_ELEMENT_KEY) or value.get("ELEMENT")
        return str(found) if found else None
    return None


def _json_or_empty(raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _observe(service: MobileRecordingService, row: MobileRecording, method: str, rest: str, body: bytes, response: httpx.Response) -> None:
    """Log the command if the segment compiler needs it; update caches."""
    if response.status_code >= 400:
        return
    if method == "GET":
        if rest == "/source":
            value = _json_or_empty(response.content).get("value")
            if isinstance(value, str):
                source_cache.put(row.id, value)
        elif rest in {"/window/rect", "/window/size", "/window/current/size"}:
            value = _json_or_empty(response.content).get("value")
            if isinstance(value, dict):
                service.set_viewport(row, value.get("width"), value.get("height"))
        return
    if method != "POST":
        return
    request_doc = _json_or_empty(body)
    find = _FIND_PATH.match(rest)
    if find:
        using, value = str(request_doc.get("using") or ""), str(request_doc.get("value") or "")
        if not using:
            return
        response_value = _json_or_empty(response.content).get("value")
        source = source_cache.get(row.id)
        locator = {"using": using, "value": value}
        if find.group(1) == "element":
            element_id = _element_id(response_value)
            attrs = element_attributes(source, using, value, row.platform, 1)[0]
            service.record(row, method=method, path=rest, request=locator, status_code=response.status_code, element_id=element_id, element=attrs)
        elif isinstance(response_value, list):
            ids = [eid for eid in (_element_id(item) for item in response_value) if eid]
            attrs_list = element_attributes(source, using, value, row.platform, len(ids))
            service.record(row, method=method, path=rest, request=locator, status_code=response.status_code, element_ids=ids, elements=attrs_list)
        return
    action = _ELEMENT_ACTION_PATH.match(rest)
    if action:
        logged: Any = None
        secret = False
        if action.group(2) == "value":
            attrs, locator_value = service.element_info(row, action.group(1))
            secret = is_secret_element(attrs, locator_value)
            text = request_doc.get("text")
            if not isinstance(text, str):
                text = "".join(str(part) for part in request_doc.get("value") or [])
            # A password field's value is withheld here, before it reaches the
            # database or the workspace.
            logged = {"text": None} if secret else {"text": text}
        service.record(row, method=method, path=rest, request=logged, status_code=response.status_code, secret=secret)
        return
    if rest in _LOGGED_ACTION_PATHS or rest.startswith("/appium/device/"):
        service.record(row, method=method, path=rest, request=request_doc or None, status_code=response.status_code)


@router.api_route("/app/mobile/wd/{recording_id}/{token}/{wd_path:path}", methods=["GET", "POST", "DELETE"])
async def webdriver_proxy(
    recording_id: str,
    token: str,
    wd_path: str,
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.get(MobileRecording, recording_id)
    if row is None or row.owner_user_id != user.id or not token_matches(row, token):
        return _wd_error(404, "invalid session id", "Unknown recording. Open the Inspector again from the Recording panel.")
    if row.status != "active":
        return _wd_error(404, "invalid session id", "This recording was closed. Open the Inspector again from the Recording panel.")
    method = request.method.upper()
    path = wd_path.strip("/")
    caps = synthetic_capabilities(row)
    if path == "status" and method == "GET":
        return JSONResponse({"value": {"ready": True, "message": "EFP recording proxy"}})
    if path == "sessions" and method == "GET":
        return JSONResponse({"value": [{"id": row.session_id, "capabilities": caps}]})
    if path == "session" and method == "POST":
        return _wd_error(403, "session not created", "This Inspector attaches to the device the assistant holds; it cannot start sessions.")
    match = _SESSION_PATH.match(path)
    if not match or match.group(1) != row.session_id:
        return _wd_error(404, "invalid session id", "Only the session the assistant published can be reached.")
    rest = (match.group(2) or "").rstrip("/")
    if ".." in rest or "//" in rest:
        return _wd_error(400, "invalid argument", "Unsupported command path.")
    if not rest:
        if method == "GET":
            return JSONResponse({"value": caps})
        if method == "DELETE":
            # Quitting in the Inspector must not end the assistant's session;
            # the assistant finishes it when the recording is done.
            return JSONResponse({"value": None})
    try:
        account = browserstack_account_for_user(db, user)
    except AppPackageError as exc:
        return _wd_error(401, "unauthorized", exc.message)
    body = await request.body()
    url = f"{row.hub_url}/session/{row.session_id}{rest}"
    try:
        async with httpx.AsyncClient(**browserstack_client_kwargs(timeout=_PROXY_TIMEOUT_SECONDS)) as client:
            upstream = await client.request(
                method,
                url,
                params=list(request.query_params.multi_items()),
                content=body or None,
                headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"},
                auth=account.auth,
            )
    except httpx.HTTPError as exc:
        return _wd_error(502, "unknown error", f"BrowserStack did not answer: {exc.__class__.__name__}")
    try:
        _observe(MobileRecordingService(db), row, method, rest, body, upstream)
    except Exception:  # the recording must never break the member's Inspector
        logger.warning("Recording %s could not log %s %s", row.id, method, rest, exc_info=True)
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


# ----- hosted Appium Inspector ------------------------------------------------------


@router.get("/inspector")
@router.get("/inspector/{asset_path:path}")
def hosted_inspector(request: Request, asset_path: str = ""):
    from app.web import _authorized_web_user  # the web module owns the login redirects

    _user, access_response = _authorized_web_user(request)
    if access_response is not None:
        return access_response
    base = appium_inspector_dir()
    if base is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The hosted Appium Inspector is not installed on this Portal")
    if not asset_path and not request.url.path.endswith("/"):
        target = "/inspector/" + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(target, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    root = base.resolve()
    candidate = (root / asset_path).resolve() if asset_path else root / "index.html"
    if root not in candidate.parents and candidate != root / "index.html":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not candidate.is_file():
        # A client-side route; the app handles it.
        candidate = root / "index.html"
    headers = {"Cache-Control": "public, max-age=31536000, immutable"} if "/assets/" in f"/{asset_path}" else {"Cache-Control": "no-cache"}
    return FileResponse(candidate, headers=headers)
