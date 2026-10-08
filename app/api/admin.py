from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.deps import require_admin
from app.repositories.audit_repo import AuditRepository
from app.repositories.agent_repo import AgentRepository
from app.schemas.admin import AuditLogResponse
from app.schemas.agent import AgentResponse
from app.services.connector_defaults_service import changed_connector_types
from app.services.connector_defaults_sync import sync_followers
from app.services.runtime_profile_references import seed_config_error
from app.services.runtime_profile_secret_service import RuntimeProfileSecretService
from app.services.runtime_profile_seed_service import RuntimeProfileSeedService
from app.utils.agent_responses import build_agent_response

router = APIRouter(prefix="/api/admin", tags=["admin"])
runtime_profile_secret_service = RuntimeProfileSecretService()


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
    """Read what every new member's default profile starts from.

    Returns the seed as stored, credentials included, so a client can read it,
    change one field, and PUT it back without silently wiping the shared
    credentials an admin configured. Admin-only, like the panel it backs.
    """

    service = RuntimeProfileSeedService(db)
    return {"seed": service.get_seed(), "summary": service.seed_summary()}


@router.put("/runtime-profile-seed")
def put_runtime_profile_seed(
    background_tasks: BackgroundTasks,
    payload: dict = Body(...),
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Replace the seed and roll the change out to the members who follow it.

    Credentials are allowed and optional: an admin seeds the ones that belong to
    a shared service account and leaves the rest blank for members to supply.
    A seed a member's own Save would refuse (a dangling default instance or
    account, a value the platform cannot store) is refused with 422. The
    members whose connectors follow the changed ones are updated after the
    response: Secrets, idle restarts, "Restart to apply" on busy assistants.
    """
    seed = payload.get("seed") if isinstance(payload, dict) and "seed" in payload else payload
    if not isinstance(seed, dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Seed must be a JSON object.")
    error = seed_config_error(seed)
    if error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=error)
    service = RuntimeProfileSeedService(db)
    old_seed = service.get_seed()
    try:
        saved = service.save_seed(seed, updated_by_user_id=admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    changed = changed_connector_types(old_seed, saved)
    AuditRepository(db).create(
        action="update_runtime_profile_seed",
        target_type="platform_setting",
        target_id="runtime_profile_seed",
        user_id=admin.id,
        details={"sections": sorted(saved.keys()), "changed_connectors": changed},
    )
    if changed:
        background_tasks.add_task(
            sync_followers,
            changed,
            secret_service=runtime_profile_secret_service,
            triggered_by_user_id=admin.id,
            session_factory=SessionLocal,
        )
    return {"seed": saved, "summary": service.seed_summary(), "changed_connectors": changed}
