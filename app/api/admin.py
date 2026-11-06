import csv
import io
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.i18n import N_
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
from app.services.usage_insights_service import UsageInsightsService
from app.services.usage_rollup_service import PERIOD_DAYS_OPTIONS, UsageRollupService
from app.utils.agent_responses import build_agent_response

router = APIRouter(prefix="/api/admin", tags=["admin"])

# CSV rows end with CR LF; the byte-order mark lets Excel open the file as UTF-8.
CRLF = chr(13) + chr(10)
BOM = chr(0xFEFF)
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
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=N_("Seed must be a JSON object."))
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


def _usage_period_or_422(days: int) -> int:
    if days not in PERIOD_DAYS_OPTIONS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=N_("days must be one of 7, 30 or 90"))
    return int(days)


@router.get("/usage/overview")
def admin_usage_overview(
    _: object = Depends(require_admin),
    db: Session = Depends(get_db),
    days: int = Query(default=30, description="Period: one of 7, 30, 90 days."),
):
    """What the platform consumed, how runs went and what members use, over the last ``days`` days.

    Consumption comes from the daily rollups (today and yesterday are refreshed
    first unless that happened in the last five minutes), quality and
    adoption from the executions, tasks, rules and member settings.
    """
    period = _usage_period_or_422(days)
    rollups = UsageRollupService(db)
    rollups.refresh_recent()
    overview = rollups.overview(period)
    insights = UsageInsightsService(db)
    overview["quality"] = insights.quality(period)
    overview["adoption"] = insights.adoption(period)
    return overview


@router.get("/usage/export.csv")
def admin_usage_export(
    _: object = Depends(require_admin),
    db: Session = Depends(get_db),
    days: int = Query(default=30),
    rows: str = Query(default="daily", description="daily: one row per day, member, kind and model; executions: one row per run."),
):
    """The period's usage as CSV, for a spreadsheet or Splunk."""
    period = _usage_period_or_422(days)
    kind = str(rows or "daily").strip().lower()
    if kind not in {"daily", "executions"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=N_("rows must be daily or executions"))
    service = UsageRollupService(db)
    service.refresh_recent()
    records = service.daily_rows(period) if kind == "daily" else service.execution_rows(period)
    buffer = io.StringIO()
    if records:
        writer = csv.DictWriter(buffer, fieldnames=list(records[0].keys()), lineterminator=CRLF)
        writer.writeheader()
        writer.writerows(records)
    else:
        buffer.write(("day,username,kind,model,executions,credits" if kind == "daily" else "created_at,username,kind,status,model,credits") + CRLF)
    filename = f"efp-usage-{kind}-{period}d-{datetime.utcnow().date().isoformat()}.csv"
    return Response(
        content=BOM + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
