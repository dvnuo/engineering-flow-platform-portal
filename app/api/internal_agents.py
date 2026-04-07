from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_internal_api_key
from app.repositories.agent_repo import AgentRepository
from app.schemas.runtime_router import AgentRuntimeContextResponse, RuntimeCapabilityContextResponse
from app.services.runtime_router import RuntimeRouterService

router = APIRouter(tags=["internal-agents"])
service = RuntimeRouterService()


@router.get("/api/internal/agents/{agent_id}/runtime-context", response_model=AgentRuntimeContextResponse)
def get_agent_runtime_context(agent_id: str, _: bool = Depends(require_internal_api_key), db: Session = Depends(get_db)):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    profile_id, resolved_profile = service.capability_context_service.resolve_for_agent(db, agent)
    capability_context = service.capability_context_service.build_runtime_capability_context(
        profile_id, resolved_profile, db=db, agent_id=agent.id
    )

    return AgentRuntimeContextResponse(
        agent_id=agent.id,
        agent_type=agent.agent_type,
        capability_profile_id=agent.capability_profile_id,
        policy_profile_id=agent.policy_profile_id,
        capability_context=RuntimeCapabilityContextResponse.model_validate(capability_context),
        runtime_target=service.resolve_agent_runtime(agent),
    )
