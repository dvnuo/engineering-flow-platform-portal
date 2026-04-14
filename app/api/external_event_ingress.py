from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
from app.schemas.external_event_ingress import ExternalEventIngressRequest, ExternalEventIngressResponse
from app.services.external_event_router import ExternalEventRouterService

router = APIRouter(tags=["external-event-ingress"])
service = ExternalEventRouterService()


@router.post("/api/external-events/ingest", response_model=ExternalEventIngressResponse)
def ingest_external_event(payload: ExternalEventIngressRequest, _=Depends(require_admin), db: Session = Depends(get_db)):
    return service.route_external_event(payload, db)


@router.post("/api/internal/external-events/ingest", response_model=ExternalEventIngressResponse)
def ingest_external_event_internal(
    payload: ExternalEventIngressRequest,
    db: Session = Depends(get_db),
):
    return service.route_external_event(payload, db)
