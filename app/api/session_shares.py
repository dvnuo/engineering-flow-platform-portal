"""Share one session read-only: create, change the audience, look it up, revoke."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.repositories.agent_repo import AgentRepository
from app.repositories.audit_repo import AuditRepository
from app.repositories.session_share_repo import SessionShareRepository
from app.schemas.session_share import SessionShareResponse, SessionShareUpsertRequest
from app.services.session_share_service import (
    SCOPE_ANYONE,
    anyone_scope_enabled,
    is_task_session_id,
    normalize_scope,
    share_url,
)

router = APIRouter(prefix="/api/agents/{agent_id}/sessions/{session_id}/share", tags=["session-shares"])

# The same prefixes app.web uses to keep task sessions out of the sessions
# panel; a task's working session is not a conversation to publish.
TASK_SESSION_ID_PREFIXES = ("agent-task-", "generic-task-", "task-")


def _can_manage(agent, user) -> bool:
    return user.role == "admin" or agent.owner_user_id == user.id


def _load_manageable_agent(agent_id: str, user, db: Session):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not _can_manage(agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the assistant's owner can share its sessions")
    return agent


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
        view_count=int(share.view_count or 0),
        last_viewed_at=share.last_viewed_at,
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
    _load_manageable_agent(agent_id, user, db)
    share = SessionShareRepository(db).get_by_agent_and_session(agent_id, session_id)
    if share is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This session is not shared")
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
    agent = _load_manageable_agent(agent_id, user, db)
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Session ID required")
    if is_task_session_id(normalized_session_id, TASK_SESSION_ID_PREFIXES):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Task sessions cannot be shared")
    try:
        scope = normalize_scope(payload.scope)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if scope == SCOPE_ANYONE and not anyone_scope_enabled():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Sharing with anyone is turned off on this Portal")
    title = (payload.title or "").strip() or None

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
    agent = _load_manageable_agent(agent_id, user, db)
    repo = SessionShareRepository(db)
    share = repo.get_by_agent_and_session(agent.id, session_id)
    if share is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This session is not shared")
    repo.delete(share)
    AuditRepository(db).create("unshare_session", "session", session_id, user.id, {"agent_id": agent.id})
    return None
