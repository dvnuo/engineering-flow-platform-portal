"""The read-only share page (``/share/{token}``) and the data it reads live from the runtime.

A share is not a snapshot: every load forwards ``GET api/sessions/{id}`` to
the assistant's runtime with the agent context the Portal builds from the
share row (``share_transcripts.load_share_transcript``), projects the answer
to the viewer-safe transcript and returns that. The viewer never chooses the
agent or the session, and nothing but the transcript and the raster images
it shows leaves through these routes. A stopped assistant is reported as
such; a view never starts it and never counts as its activity.

These are the first routes of the Portal a visitor may reach without a
session cookie (``anyone`` shares); ``members`` shares go through the same
cookie-and-allowlist check as every other page.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
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
from app.services.locale_service import resolve_locale_for_request
from app.services.session_context_preview import parse_metadata_json
from app.services.session_share_service import effective_scope, is_inline_image_type, share_path, viewer_may_open
from app.services.share_transcripts import STATUS_OK, load_share_transcript

logger = logging.getLogger(__name__)

router = APIRouter(tags=["session-share-pages"])

DENIED_MISSING = "missing"
DENIED_SIGN_IN = "sign_in_required"
DENIED_NOT_ALLOWLISTED = "not_allowlisted"


@dataclass
class ShareView:
    """A share a viewer may open: the row, its assistant, and the member looking (None for a visitor)."""

    share: Any
    agent: Any
    user: Any


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


def resolve_share_view(db, request: Request, token: str) -> tuple[Optional[ShareView], Optional[str]]:
    """``(view, None)`` when the request may open the share, else ``(None, why)``.

    The same gate for the page, its data and its files: the share must exist
    with its assistant, and either it is open to anyone or the request
    carries a signed-in, allowlisted member's session.
    """

    share = SessionShareRepository(db).get_by_token(token)
    if share is None:
        return None, DENIED_MISSING
    user, reason = portal_web._session_user_access(request)
    if reason is not None:
        user = None
    if not viewer_may_open(share, user):
        return None, DENIED_NOT_ALLOWLISTED if reason == "not_allowlisted" else DENIED_SIGN_IN
    agent = AgentRepository(db).get_by_id(share.agent_id)
    if agent is None:
        return None, DENIED_MISSING
    return ShareView(share=share, agent=agent, user=user), None


def _login_redirect_url(token: str) -> str:
    return f"/login?next={quote(share_path(token), safe='/')}"


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _share_page_context(request: Request, db, view: ShareView) -> dict[str, Any]:
    return {
        "request": request,
        "title": _stored_title(db, view.share),
        "token": view.share.token,
        "data_url": f"{share_path(view.share.token)}/data.json",
        "assistant_name": str(getattr(view.agent, "name", "") or "Assistant"),
        "owner_display_name": _owner_display_name(db, view.share),
        "scope": effective_scope(view.share),
        "missing": False,
        # The viewer's language, not the owner's: a signed-in member's
        # preference, else the browser's (the transcript stays as it was).
        "locale": resolve_locale_for_request(request, view.user).ui_locale,
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
        view, denied = resolve_share_view(db, request, token)
        if view is None:
            if denied == DENIED_MISSING:
                return _missing_page(request)
            if denied == DENIED_NOT_ALLOWLISTED:
                return portal_web._redirect_to_unauthorized()
            return RedirectResponse(url=_login_redirect_url(token), status_code=302)
        return portal_web.templates.TemplateResponse("share.html", _share_page_context(request, db, view))
    finally:
        db.close()


@router.get("/share/{token}/data.json")
async def share_data(request: Request, token: str):
    db = SessionLocal()
    try:
        view, denied = resolve_share_view(db, request, token)
        if view is None:
            if denied == DENIED_MISSING:
                return JSONResponse({"error": "not_found"}, status_code=404)
            if denied == DENIED_NOT_ALLOWLISTED:
                return JSONResponse({"error": "not_allowlisted", "login_url": "/unauthorized"}, status_code=403)
            return JSONResponse({"error": "sign_in_required", "login_url": _login_redirect_url(token)}, status_code=401)

        # Refresh skips the short cache for a member; a visitor's Refresh is
        # served from it, so a link passed around cannot be turned into a
        # stream of session reads against the pod.
        fresh = view.user is not None and _truthy(request.query_params.get("fresh"))
        transcript = await load_share_transcript(view.share, view.agent, portal_web._forward_runtime, fresh=fresh)

        can_open = bool(view.user is not None and portal_web._can_access(view.agent, view.user))
        agent_id = str(view.agent.id)
        return JSONResponse(
            {
                "status": transcript["status"],
                "title": transcript.get("name") or _stored_title(db, view.share),
                "assistant_name": str(getattr(view.agent, "name", "") or "Assistant"),
                "owner_display_name": _owner_display_name(db, view.share),
                "scope": effective_scope(view.share),
                "loaded_at": _now_iso(),
                "running": bool(transcript.get("running")),
                "can_open_in_portal": can_open,
                # Open in Portal needs the assistant and the session: the page
                # remembers the session as the assistant's last one and opens it.
                "portal_url": f"/app#/assistants/{quote(agent_id, safe='')}" if can_open else None,
                "agent_id": agent_id if can_open else None,
                "session_id": str(view.share.session_id) if can_open else None,
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
    """A raster image of the shared session, and only one the transcript shows inline."""

    db = SessionLocal()
    try:
        view, denied = resolve_share_view(db, request, token)
        if view is None:
            if denied == DENIED_MISSING:
                return Response(status_code=404)
            return Response(status_code=403 if denied == DENIED_NOT_ALLOWLISTED else 401)

        transcript = await load_share_transcript(view.share, view.agent, portal_web._forward_runtime)
        if transcript["status"] != STATUS_OK or file_id not in transcript["file_ids"]:
            return Response(status_code=404)

        try:
            forwarded = await portal_web._forward_runtime(
                user=None,
                agent=view.agent,
                method="GET",
                subpath=f"api/files/{quote(file_id, safe='')}",
                query_items=[("session_id", view.share.session_id)],
                body=None,
                return_response_headers=True,
            )
        except Exception:  # noqa: BLE001 - the runtime being down is a missing image, not a 500
            logger.info("Shared session %s: file %s could not be read from the runtime", token, file_id, exc_info=True)
            return Response(status_code=404)
        status_code, content, content_type, upstream_headers = forwarded
        # The transcript lists the attachment's declared type; the bytes the
        # runtime serves must be a raster image too (never SVG or markup).
        if status_code != 200 or not is_inline_image_type(content_type):
            return Response(status_code=404)
        headers = _harden_attachment_response_headers(content_type, upstream_headers)
        headers["Cache-Control"] = "private, max-age=300"
        return Response(content=content, media_type=content_type, headers=headers)
    finally:
        db.close()
