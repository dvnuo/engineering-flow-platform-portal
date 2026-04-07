from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.repositories.runtime_capability_catalog_snapshot_repo import RuntimeCapabilityCatalogSnapshotRepository
from app.schemas.runtime_capability_catalog import RuntimeCapabilityCatalogSnapshotResponse, RuntimeCapabilityCatalogSyncRequest
from app.services.runtime_capability_sync_service import RuntimeCapabilitySyncError, RuntimeCapabilitySyncService

router = APIRouter(prefix="/api/runtime-capability-catalog", tags=["runtime-capability-catalog"])
service = RuntimeCapabilitySyncService()


@router.post("/sync", response_model=RuntimeCapabilityCatalogSnapshotResponse)
def sync_runtime_capability_catalog(payload: RuntimeCapabilityCatalogSyncRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
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
    _ = user
    repo = RuntimeCapabilityCatalogSnapshotRepository(db)
    snapshot = repo.get_latest_for_agent(agent_id) if agent_id else repo.get_latest()
    if not snapshot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No runtime capability catalog snapshot found")
    return RuntimeCapabilityCatalogSnapshotResponse.model_validate(snapshot)
