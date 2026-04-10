from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.repositories.agent_repo import AgentRepository
from app.repositories.runtime_capability_catalog_snapshot_repo import RuntimeCapabilityCatalogSnapshotRepository
from app.schemas.runtime_capability_catalog import RuntimeCapabilityCatalogSnapshotResponse, RuntimeCapabilityCatalogSyncRequest
from app.services.runtime_capability_sync_service import RuntimeCapabilitySyncError, RuntimeCapabilitySyncService

router = APIRouter(prefix="/api/runtime-capability-catalog", tags=["runtime-capability-catalog"])
service = RuntimeCapabilitySyncService()


def _can_write(agent, user) -> bool:
    return user.role == "admin" or agent.owner_user_id == user.id


@router.post("/sync", response_model=RuntimeCapabilityCatalogSnapshotResponse)
def sync_runtime_capability_catalog(payload: RuntimeCapabilityCatalogSyncRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    agent = AgentRepository(db).get_by_id(payload.agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not _can_write(agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    try:
        snapshot = service.sync_from_agent_runtime(db=db, agent_id=payload.agent_id)
    except RuntimeCapabilitySyncError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return RuntimeCapabilityCatalogSnapshotResponse.model_validate(snapshot)


@router.get("/latest", response_model=RuntimeCapabilityCatalogSnapshotResponse)
def get_latest_runtime_capability_catalog(
    agent_id: str | None = None,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repo = RuntimeCapabilityCatalogSnapshotRepository(db)
    if agent_id is not None:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
        if not _can_write(agent, user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        snapshot = repo.get_latest_for_agent(agent_id)
    else:
        if user.role != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        snapshot = repo.get_latest()
    if not snapshot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No runtime capability catalog snapshot found")
    return RuntimeCapabilityCatalogSnapshotResponse.model_validate(snapshot)
