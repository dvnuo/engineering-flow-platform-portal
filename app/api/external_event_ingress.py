from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.schemas.external_event_ingress import ExternalEventIngressRequest, ExternalEventIngressResponse
from app.services.external_event_router import ExternalEventRouterService

router = APIRouter(tags=["external-event-ingress"])
service = ExternalEventRouterService()


@router.post("/api/external-events/ingest", response_model=ExternalEventIngressResponse)
def ingest_external_event(payload: ExternalEventIngressRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    return service.route_external_event(payload, db)
