from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_internal_api_key
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_session_metadata_repo import AgentSessionMetadataRepository
from app.schemas.agent_session_metadata import AgentSessionMetadataResponse, AgentSessionMetadataUpsertRequest

router = APIRouter(tags=["internal-session-metadata"])


@router.put(
    "/api/internal/agents/{agent_id}/sessions/{session_id}/metadata",
    response_model=AgentSessionMetadataResponse,
)
def upsert_session_metadata(
    agent_id: str,
    session_id: str,
    payload: AgentSessionMetadataUpsertRequest,
    _: bool = Depends(require_internal_api_key),
    db: Session = Depends(get_db),
):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    repo = AgentSessionMetadataRepository(db)
    record = repo.upsert(agent_id=agent_id, session_id=session_id, **payload.model_dump())
    return AgentSessionMetadataResponse.model_validate(record)


@router.get(
    "/api/internal/agents/{agent_id}/sessions/{session_id}/metadata",
    response_model=AgentSessionMetadataResponse,
)
def get_session_metadata(
    agent_id: str,
    session_id: str,
    _: bool = Depends(require_internal_api_key),
    db: Session = Depends(get_db),
):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    record = AgentSessionMetadataRepository(db).get_by_agent_and_session(agent_id=agent_id, session_id=session_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session metadata not found")

    return AgentSessionMetadataResponse.model_validate(record)


@router.get(
    "/api/internal/agents/{agent_id}/sessions/metadata",
    response_model=list[AgentSessionMetadataResponse],
)
def list_session_metadata(
    agent_id: str,
    group_id: str | None = None,
    latest_event_state: str | None = None,
    current_task_id: str | None = None,
    _: bool = Depends(require_internal_api_key),
    db: Session = Depends(get_db),
):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    records = AgentSessionMetadataRepository(db).list_by_agent(
        agent_id,
        group_id=group_id,
        latest_event_state=latest_event_state,
        current_task_id=current_task_id,
    )
    return [AgentSessionMetadataResponse.model_validate(item) for item in records]
