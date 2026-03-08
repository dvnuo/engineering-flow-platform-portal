from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
from app.repositories.audit_repo import AuditRepository
from app.repositories.agent_repo import AgentRepository
from app.schemas.admin import AuditLogResponse
from app.schemas.agent import AgentResponse

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/agents", response_model=list[AgentResponse])
def admin_agents(_: object = Depends(require_admin), db: Session = Depends(get_db)):
    agents = AgentRepository(db).list_all()
    return [AgentResponse.model_validate(r) for r in agents]


@router.get("/audit-logs", response_model=list[AuditLogResponse])
def audit_logs(_: object = Depends(require_admin), db: Session = Depends(get_db)):
    rows = AuditRepository(db).list_all()
    return [AuditLogResponse.model_validate(r) for r in rows]
