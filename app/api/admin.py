from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
from app.repositories.audit_repo import AuditRepository
from app.repositories.agent_repo import AgentRepository
from app.schemas.admin import AuditLogResponse
from app.schemas.agent import AgentResponse
from app.services.runtime_profile_seed_service import (
    RuntimeProfileSeedService,
    SeedContainsSecretError,
)
from app.utils.agent_responses import build_agent_response

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/agents", response_model=list[AgentResponse])
def admin_agents(_: object = Depends(require_admin), db: Session = Depends(get_db)):
    agents = AgentRepository(db).list_all()
    return [build_agent_response(r) for r in agents]


@router.get("/audit-logs", response_model=list[AuditLogResponse])
def audit_logs(_: object = Depends(require_admin), db: Session = Depends(get_db)):
    rows = AuditRepository(db).list_all()
    return [AuditLogResponse.model_validate(r) for r in rows]


@router.get("/runtime-profile-seed")
def get_runtime_profile_seed(_: object = Depends(require_admin), db: Session = Depends(get_db)):
    """Read the connection shape every new member's default profile starts from."""

    service = RuntimeProfileSeedService(db)
    return {"seed": service.get_seed(), "summary": service.seed_summary()}


@router.put("/runtime-profile-seed")
def put_runtime_profile_seed(
    payload: dict = Body(...),
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Replace the seed.

    Rejected outright if it carries a credential: the platform must never hold a
    member's secret on their behalf, and enforcing that here means the rule is
    checked by code rather than trusted to whoever edits the form.
    """
    seed = payload.get("seed") if isinstance(payload, dict) and "seed" in payload else payload
    service = RuntimeProfileSeedService(db)
    try:
        saved = service.save_seed(seed, updated_by_user_id=admin.id)
    except SeedContainsSecretError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    AuditRepository(db).create(
        action="update_runtime_profile_seed",
        target_type="platform_setting",
        target_id="runtime_profile_seed",
        user_id=admin.id,
        details={"sections": sorted(saved.keys())},
    )
    return {"seed": saved, "summary": service.seed_summary()}
