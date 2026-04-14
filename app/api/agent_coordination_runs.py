from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.repositories.agent_coordination_run_repo import AgentCoordinationRunRepository
from app.schemas.agent_coordination_run import AgentCoordinationRunResponse

router = APIRouter(tags=["agent-coordination-runs"])


@router.get("/api/internal/agent-groups/{group_id}/coordination-runs", response_model=list[AgentCoordinationRunResponse])
def list_group_coordination_runs(
    group_id: str,
    db: Session = Depends(get_db),
):
    rows = AgentCoordinationRunRepository(db).list_by_group_id(group_id)
    return [AgentCoordinationRunResponse.from_model(row) for row in rows]


@router.get("/api/internal/coordination-runs/{coordination_run_id}", response_model=AgentCoordinationRunResponse)
def get_coordination_run(
    coordination_run_id: str,
    db: Session = Depends(get_db),
):
    row = AgentCoordinationRunRepository(db).get_by_coordination_run_id(coordination_run_id)
    if not row:
        raise HTTPException(status_code=404, detail="Coordination run not found")
    return AgentCoordinationRunResponse.from_model(row)
