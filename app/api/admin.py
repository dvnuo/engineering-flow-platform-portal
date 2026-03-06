from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
from app.repositories.audit_repo import AuditRepository
from app.repositories.robot_repo import RobotRepository
from app.schemas.admin import AuditLogResponse
from app.schemas.robot import RobotResponse

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/robots", response_model=list[RobotResponse])
def admin_robots(_: object = Depends(require_admin), db: Session = Depends(get_db)):
    robots = RobotRepository(db).list_all()
    return [RobotResponse.model_validate(r) for r in robots]


@router.get("/audit-logs", response_model=list[AuditLogResponse])
def audit_logs(_: object = Depends(require_admin), db: Session = Depends(get_db)):
    rows = AuditRepository(db).list_all()
    return [AuditLogResponse.model_validate(r) for r in rows]
