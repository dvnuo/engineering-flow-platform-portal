"""The read-only share page (``/share/{token}``) and the data it reads live from the runtime.

A share is not a snapshot: every load forwards ``GET api/sessions/{id}`` to
the assistant's runtime with the agent context the Portal builds from the
share row, projects the answer to the viewer-safe transcript
(``session_share_service.project_shared_transcript``) and returns that. The
viewer never chooses the agent or the session, and nothing but the transcript
and whitelisted image attachments leaves through these routes. A stopped
assistant is reported as such; a view never starts it and never counts as its
activity.

These are the first routes of the Portal a visitor may reach without a
session cookie (``anyone`` shares); ``members`` shares go through the same
cookie-and-allowlist check as every other page.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app import web as portal_web
from app.api.proxy import _harden_attachment_response_headers
from app.db import SessionLocal
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_session_metadata_repo import AgentSessionMetadataRepository
from app.repositories.session_share_repo import SessionShareRepository
from app.repositories.user_repo import UserRepository
from app.services.session_context_preview import parse_metadata_json
from app.services.session_share_service import (
    effective_scope,
    project_shared_transcript,
    share_path,
    transcript_file_ids,
    viewer_may_open,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["session-share-pages"])

# A page left open on auto-refresh, or several viewers of one link, must not
# turn into a stream of session reads against the pod: one runtime read per
# share per window serves them all.
RUNTIME_CACHE_SECONDS = 5.0
_RUNNING_STATES = {"running", "accepted", "queued", "in_progress"}
_runtime_cache: dict[str, tuple[float, dict[str, Any]]] = {}

STATUS_OK = "ok"
STATUS_ASSISTANT_STOPPED = "assistant_stopped"
STATUS_SESSION_MISSING = "session_missing"
STATUS_RUNTIME_UNREACHABLE = "runtime_unreachable"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _owner_display_name(db, share) -> str:
    owner = UserRepository(db).get_by_id(share.owner_user_id)
    if owner is None:
        return "a member"
    return str(owner.nickname or owner.username or "a member")


def _stored_title(db, share) -> str:
    """The name to show when the live one cannot be read: the runtime's last published name, else the name at share time."""

    record = AgentSessionMetadataRepository(db).get_by_agent_and_session(agent_id=share.agent_id, session_id=share.session_id)
    metadata = parse_metadata_json(getattr(record, "metadata_json", None)) if record else {}
    title = metadata.get("session_title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return str(share.title or "").strip() or "Shared session"


def _viewer(request: Request):
    """(user, None) for a signed-in allowlisted member, else (None, reason)."""

    user, reason = portal_web._session_user_access(request)
    if reason is None:
        return user, None
    return None, reason


def _login_redirect_url(share) -> str:
    return f"/login?next={quote(share_path(share.token), safe='/')}"


def _access_denied_response(share, reason: str, *, as_json: bool):
    if reason == "not_allowlisted":
        if as_json:
            return JSONResponse({"error": "not_allowlisted", "login_url": "/unauthorized"}, status_code=403)
        return portal_web._redirect_to_unauthorized()
    login_url = _login_redirect_url(share)
    if as_json:
        return JSONResponse({"error": "sign_in_required", "login_url": login_url}, status_code=401)
    return RedirectResponse(url=login_url, status_code=302)


def _cache_get(token: str) -> Optional[dict[str, Any]]:
    entry = _runtime_cache.get(token)
    if entry is None:
        return None
    stored_at, payload = entry
    if time.monotonic() - stored_at > RUNTIME_CACHE_SECONDS:
        _runtime_cache.pop(token, None)
        return None
    return payload


def _cache_put(token: str, payload: dict[str, Any]) -> None:
    _runtime_cache[token] = (time.monotonic(), payload)


def clear_runtime_cache() -> None:
    _runtime_cache.clear()


async def _load_transcript(share, agent, *, fresh: bool = False) -> dict[str, Any]:
    """The live transcript of the shared session, or why it cannot be read right now."""

    if str(getattr(agent, "status", "") or "").strip().lower() != "running":
        return {"status": STATUS_ASSISTANT_STOPPED, "messages": [], "name": None, "running": False, "error": None}

    if not fresh:
        cached = _cache_get(share.token)
        if cached is not None:
            return cached

    try:
        status_code, content, _content_type = await portal_web._forward_runtime(
            user=None,
            agent=agent,
            method="GET",
            subpath=f"api/sessions/{quote(share.session_id, safe='')}",
            query_items=[],
            body=None,
        )
    except Exception as exc:  # noqa: BLE001 - the runtime being down is a page state, not a 500
        logger.info("Shared session %s could not reach its runtime: %s", share.token, exc)
        return {"status": STATUS_RUNTIME_UNREACHABLE, "messages": [], "name": None, "running": False, "error": "The assistant did not answer."}

    if status_code == 404:
        result = {"status": STATUS_SESSION_MISSING, "messages": [], "name": None, "running": False, "error": None}
        _cache_put(share.token, result)
        return result
    if status_code >= 400:
        detail = portal_web._normalize_runtime_error_detail(content)
        return {"status": STATUS_RUNTIME_UNREACHABLE, "messages": [], "name": None, "running": False, "error": detail or f"Runtime answered {status_code}."}

    try:
        payload = json.loads(content.decode("utf-8"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"status": STATUS_RUNTIME_UNREACHABLE, "messages": [], "name": None, "running": False, "error": "The runtime's answer could not be read."}
    if not isinstance(payload, dict):
        payload = {}

    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    last_runtime_status = str(metadata.get("last_runtime_status") or "").strip().lower()
    name = payload.get("name")
    result = {
        "status": STATUS_OK,
        "messages": project_shared_transcript(payload, assistant_name=str(getattr(agent, "name", "") or "")),
        "name": name.strip() if isinstance(name, str) and name.strip() else None,
        "running": last_runtime_status in _RUNNING_STATES,
        "error": None,
    }
    _cache_put(share.token, result)
    return result


def _share_context(request: Request, db, share, *, transcript_title: Optional[str] = None) -> dict[str, Any]:
    agent = AgentRepository(db).get_by_id(share.agent_id)
    return {
        "request": request,
        "title": transcript_title or _stored_title(db, share),
        "token": share.token,
        "data_url": f"{share_path(share.token)}/data.json",
        "assistant_name": str(getattr(agent, "name", "") or "Assistant"),
        "owner_display_name": _owner_display_name(db, share),
        "scope": effective_scope(share),
        "missing": False,
    }


def _missing_page(request: Request) -> Response:
    return portal_web.templates.TemplateResponse(
        "share.html",
        {
            "request": request,
            "title": "This link is no longer available",
            "token": "",
            "data_url": "",
            "assistant_name": "",
            "owner_display_name": "",
            "scope": "",
            "missing": True,
        },
        status_code=404,
    )


@router.get("/share/{token}", response_class=HTMLResponse)
def share_page(request: Request, token: str):
    db = SessionLocal()
    try:
        share = SessionShareRepository(db).get_by_token(token)
        if share is None:
            return _missing_page(request)
        user, reason = _viewer(request)
        if not viewer_may_open(share, user):
            return _access_denied_response(share, reason or "missing_session", as_json=False)
        agent = AgentRepository(db).get_by_id(share.agent_id)
        if agent is None:
            return _missing_page(request)
        return portal_web.templates.TemplateResponse("share.html", _share_context(request, db, share))
    finally:
        db.close()


@router.get("/share/{token}/data.json")
async def share_data(request: Request, token: str):
    db = SessionLocal()
    try:
        repo = SessionShareRepository(db)
        share = repo.get_by_token(token)
        if share is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        user, reason = _viewer(request)
        if not viewer_may_open(share, user):
            return _access_denied_response(share, reason or "missing_session", as_json=True)
        agent = AgentRepository(db).get_by_id(share.agent_id)
        if agent is None:
            return JSONResponse({"error": "not_found"}, status_code=404)

        fresh = str(request.query_params.get("fresh") or "").strip().lower() in {"1", "true", "yes"}
        transcript = await _load_transcript(share, agent, fresh=fresh)
        if transcript["status"] == STATUS_OK:
            try:
                repo.record_view(share)
            except Exception:  # noqa: BLE001 - counters never fail a read
                logger.debug("share view counter failed", exc_info=True)

        can_open = bool(user is not None and portal_web._can_access(agent, user))
        return JSONResponse(
            {
                "status": transcript["status"],
                "title": transcript.get("name") or _stored_title(db, share),
                "assistant_name": str(getattr(agent, "name", "") or "Assistant"),
                "owner_display_name": _owner_display_name(db, share),
                "scope": effective_scope(share),
                "loaded_at": _now_iso(),
                "running": bool(transcript.get("running")),
                "can_open_in_portal": can_open,
                "portal_url": f"/app#/assistants/{quote(str(agent.id), safe='')}" if can_open else None,
                "messages": transcript["messages"],
                "message_count": len(transcript["messages"]),
                "error": transcript.get("error"),
            },
            headers={"Cache-Control": "no-store"},
        )
    finally:
        db.close()


@router.get("/share/{token}/files/{file_id}")
async def share_file(request: Request, token: str, file_id: str):
    """An image attachment of the shared session, and only one the transcript shows."""

    db = SessionLocal()
    try:
        share = SessionShareRepository(db).get_by_token(token)
        if share is None:
            return Response(status_code=404)
        user, reason = _viewer(request)
        if not viewer_may_open(share, user):
            return Response(status_code=401 if reason != "not_allowlisted" else 403)
        agent = AgentRepository(db).get_by_id(share.agent_id)
        if agent is None:
            return Response(status_code=404)

        transcript = await _load_transcript(share, agent)
        if transcript["status"] != STATUS_OK or file_id not in transcript_file_ids(transcript["messages"]):
            return Response(status_code=404)

        try:
            forwarded = await portal_web._forward_runtime(
                user=None,
                agent=agent,
                method="GET",
                subpath=f"api/files/{quote(file_id, safe='')}",
                query_items=[("session_id", share.session_id)],
                body=None,
                return_response_headers=True,
            )
        except Exception:  # noqa: BLE001
            return Response(status_code=404)
        status_code, content, content_type, upstream_headers = forwarded
        base_type = str(content_type or "").split(";")[0].strip().lower()
        if status_code != 200 or not base_type.startswith("image/"):
            return Response(status_code=404)
        headers = _harden_attachment_response_headers(content_type, upstream_headers)
        headers["Content-Disposition"] = "inline"
        headers["Cache-Control"] = "private, max-age=300"
        return Response(content=content, media_type=content_type, headers=headers)
    finally:
        db.close()
