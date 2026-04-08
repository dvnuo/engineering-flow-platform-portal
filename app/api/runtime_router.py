from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
from app.schemas.runtime_router import ResolveBindingRequest, RuntimeRoutingDecisionResponse
from app.services.runtime_router import RuntimeRouterService

router = APIRouter(prefix="/api/runtime-router", tags=["runtime-router"])
service = RuntimeRouterService()


@router.post("/resolve-binding", response_model=RuntimeRoutingDecisionResponse)
def resolve_binding(payload: ResolveBindingRequest, _=Depends(require_admin), db: Session = Depends(get_db)):
    return service.resolve_binding_decision(payload.system_type, payload.external_account_id, db)
