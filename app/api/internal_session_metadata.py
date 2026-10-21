import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_session_metadata_repo import AgentSessionMetadataRepository
from app.schemas.agent_session_metadata import (
    AgentSessionMetadataResponse,
    AgentSessionMetadataUpsertRequest,
    SessionTitleUpdateRequest,
)
from app.services.session_context_preview import parse_metadata_json, serialize_agent_session_metadata_with_preview

router = APIRouter(tags=["internal-session-metadata"])

@router.put("/api/internal/agents/{agent_id}/sessions/{session_id}/metadata", response_model=AgentSessionMetadataResponse)
def upsert_session_metadata(agent_id: str, session_id: str, payload: AgentSessionMetadataUpsertRequest, db: Session = Depends(get_db)):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    record = AgentSessionMetadataRepository(db).upsert(agent_id=agent_id, session_id=session_id, **payload.model_dump())
    return AgentSessionMetadataResponse.model_validate(serialize_agent_session_metadata_with_preview(record))


@router.patch("/api/internal/agents/{agent_id}/sessions/{session_id}/title")
def update_session_title(agent_id: str, session_id: str, payload: SessionTitleUpdateRequest, db: Session = Depends(get_db)):
    """The runtime named (or renamed) a session: keep only ``session_title`` in the row's metadata.

    A full metadata publish replaces every column of the row, and the title
    arrives in the background with no run state to send alongside it, so the
    runtime sends it on its own. The Portal shows it where the live name cannot
    be read (a stopped pod): the sessions panel's metadata-only rows and the
    share page.
    """
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    title = str(payload.title or "").strip()
    if not title:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="title required")
    repo = AgentSessionMetadataRepository(db)
    record = repo.get_by_agent_and_session(agent_id=agent_id, session_id=session_id)
    metadata = parse_metadata_json(getattr(record, "metadata_json", None)) if record else {}
    metadata["session_title"] = title
    repo.upsert(agent_id=agent_id, session_id=session_id, metadata_json=json.dumps(metadata, ensure_ascii=False))
    return {"success": True, "agent_id": agent_id, "session_id": session_id, "title": title}

@router.get("/api/internal/agents/{agent_id}/sessions/{session_id}/metadata", response_model=AgentSessionMetadataResponse)
def get_session_metadata(agent_id: str, session_id: str, include_deleted: bool = False, db: Session = Depends(get_db)):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    record = AgentSessionMetadataRepository(db).get_by_agent_and_session(agent_id=agent_id, session_id=session_id, include_deleted=include_deleted)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session metadata not found")
    return AgentSessionMetadataResponse.model_validate(serialize_agent_session_metadata_with_preview(record))

@router.get("/api/internal/agents/{agent_id}/sessions/metadata", response_model=list[AgentSessionMetadataResponse])
def list_session_metadata(agent_id: str, latest_event_state: str | None = None, current_task_id: str | None = None, include_deleted: bool = False, db: Session = Depends(get_db)):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    records = AgentSessionMetadataRepository(db).list_by_agent(agent_id, latest_event_state=latest_event_state, current_task_id=current_task_id, include_deleted=include_deleted)
    return [AgentSessionMetadataResponse.model_validate(serialize_agent_session_metadata_with_preview(item)) for item in records]

@router.delete("/api/internal/agents/{agent_id}/sessions/{session_id}/metadata")
def delete_session_metadata(agent_id: str, session_id: str, db: Session = Depends(get_db)):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    record, already_deleted = AgentSessionMetadataRepository(db).mark_deleted(agent_id=agent_id, session_id=session_id)
    return {
        "success": True,
        "agent_id": agent_id,
        "session_id": session_id,
        "deleted_at": record.deleted_at.isoformat() if record.deleted_at else None,
        "already_deleted": already_deleted,
    }
