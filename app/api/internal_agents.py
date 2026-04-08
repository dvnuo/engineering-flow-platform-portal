from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_internal_api_key
from app.repositories.agent_repo import AgentRepository
from app.schemas.runtime_router import AgentRuntimeContextResponse, RuntimeCapabilityContextResponse, RuntimePolicyContextResponse
from app.services.runtime_execution_context_service import RuntimeExecutionContextService
from app.services.runtime_router import RuntimeRouterService

router = APIRouter(tags=["internal-agents"])
service = RuntimeRouterService()
runtime_execution_context_service = RuntimeExecutionContextService()


@router.get("/api/internal/agents/{agent_id}/runtime-context", response_model=AgentRuntimeContextResponse)
def get_agent_runtime_context(agent_id: str, _: bool = Depends(require_internal_api_key), db: Session = Depends(get_db)):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    execution_context = runtime_execution_context_service.build_for_agent(db, agent)

    return AgentRuntimeContextResponse(
        agent_id=agent.id,
        agent_type=agent.agent_type,
        capability_profile_id=execution_context.get("capability_profile_id"),
        policy_profile_id=execution_context.get("policy_profile_id"),
        capability_context=RuntimeCapabilityContextResponse.model_validate(execution_context.get("capability_context") or {}),
        policy_context=RuntimePolicyContextResponse.model_validate(execution_context.get("policy_context") or {}),
        runtime_target=service.resolve_agent_runtime(agent),
    )
