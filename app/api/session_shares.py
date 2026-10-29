"""Share one session read-only: create, change the audience, look it up, revoke."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.i18n import N_
from app.api.agents import _load_writable_agent
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.repositories.audit_repo import AuditRepository
from app.repositories.session_share_repo import SessionShareRepository
from app.schemas.session_share import SessionShareResponse, SessionShareUpsertRequest
from app.services.session_share_service import (
    SCOPE_ANYONE,
    anyone_scope_enabled,
    normalize_scope,
    normalize_share_title,
    share_url,
)
from app.services.share_transcripts import invalidate_share_transcript
from app.utils.task_sessions import is_task_session_id

router = APIRouter(prefix="/api/agents/{agent_id}/sessions/{session_id}/share", tags=["session-shares"])


def _portal_origin(request: Request) -> str:
    return (get_settings().base_uri or str(request.base_url)).rstrip("/")


def _response(request: Request, share) -> SessionShareResponse:
    return SessionShareResponse(
        token=share.token,
        url=share_url(_portal_origin(request), share.token),
        scope=share.scope,
        agent_id=share.agent_id,
        session_id=share.session_id,
        title=share.title,
        created_at=share.created_at,
        updated_at=share.updated_at,
        anyone_enabled=anyone_scope_enabled(),
    )


@router.get("", response_model=SessionShareResponse)
def get_session_share(
    agent_id: str,
    session_id: str,
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _load_writable_agent(agent_id, user, db)
    share = SessionShareRepository(db).get_by_agent_and_session(agent_id, session_id)
    if share is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=N_("This session is not shared"))
    return _response(request, share)


@router.post("", response_model=SessionShareResponse)
def upsert_session_share(
    agent_id: str,
    session_id: str,
    payload: SessionShareUpsertRequest,
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _repo, agent = _load_writable_agent(agent_id, user, db)
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=N_("Session ID required"))
    if is_task_session_id(normalized_session_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=N_("Task sessions cannot be shared"))
    try:
        scope = normalize_scope(payload.scope)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if scope == SCOPE_ANYONE and not anyone_scope_enabled():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=N_("Sharing with anyone is turned off on this Portal"))
    title = normalize_share_title(payload.title)

    repo = SessionShareRepository(db)
    share = repo.get_by_agent_and_session(agent.id, normalized_session_id)
    if share is None:
        share = repo.create(
            agent_id=agent.id,
            session_id=normalized_session_id,
            owner_user_id=user.id,
            scope=scope,
            title=title,
        )
        AuditRepository(db).create(
            "share_session",
            "session",
            normalized_session_id,
            user.id,
            {"agent_id": agent.id, "scope": scope},
        )
    else:
        changed = share.scope != scope or (title is not None and title != share.title)
        share = repo.update(share, scope=scope, title=title)
        if changed:
            AuditRepository(db).create(
                "update_session_share",
                "session",
                normalized_session_id,
                user.id,
                {"agent_id": agent.id, "scope": scope},
            )
    return _response(request, share)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session_share(
    agent_id: str,
    session_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _repo, agent = _load_writable_agent(agent_id, user, db)
    repo = SessionShareRepository(db)
    share = repo.get_by_agent_and_session(agent.id, session_id)
    if share is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=N_("This session is not shared"))
    token = share.token
    repo.delete(share)
    invalidate_share_transcript(token)
    AuditRepository(db).create("unshare_session", "session", session_id, user.id, {"agent_id": agent.id})
    return None
