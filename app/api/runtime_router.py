from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.schemas.runtime_router import ResolveBindingRequest, RuntimeRoutingDecisionResponse
from app.services.runtime_router import RuntimeRouterService

router = APIRouter(prefix="/api/runtime-router", tags=["runtime-router"])
service = RuntimeRouterService()


@router.post("/resolve-binding", response_model=RuntimeRoutingDecisionResponse)
def resolve_binding(payload: ResolveBindingRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    return service.resolve_binding_decision(payload.system_type, payload.external_account_id, db)
