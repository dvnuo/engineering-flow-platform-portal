from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.services.dashboard_summary import DashboardSummaryService

router = APIRouter(prefix="/api/portal", tags=["portal"])


@router.get("/dashboard-summary")
def dashboard_summary(
    scope: str = Query(default="all", pattern="^(all|mine)$"),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return DashboardSummaryService(db).build(user, scope=scope)
