from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.repositories.agent_repo import AgentRepository
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.schemas.agent_runtime_context import (
    AgentRuntimeContextResponse,
    RuntimeProfileContextResponse,
    RuntimeTargetInfoResponse,
)
from app.services.runtime_execution_context_service import RuntimeExecutionContextService
from app.services.runtime_profile_context_projection import runtime_profile_managed_sections
from app.services.runtime_profile_sync_service import RuntimeProfileSyncService

router = APIRouter(tags=["internal-agents"])
runtime_execution_context_service = RuntimeExecutionContextService()
runtime_profile_sync_service = RuntimeProfileSyncService()


@router.get("/api/internal/agents/{agent_id}/runtime-context", response_model=AgentRuntimeContextResponse)
def get_agent_runtime_context(agent_id: str, db: Session = Depends(get_db)):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    execution_context = runtime_execution_context_service.build_for_agent(db, agent)

    runtime_profile_context = None
    if agent.runtime_profile_id:
        runtime_profile = RuntimeProfileRepository(db).get_by_id(agent.runtime_profile_id)
        if runtime_profile:
            payload = runtime_profile_sync_service.build_apply_payload_for_agent(db, agent, runtime_profile)
            config = payload.get("config") or {}
            runtime_profile_context = RuntimeProfileContextResponse(
                runtime_profile_id=runtime_profile.id,
                name=runtime_profile.name,
                revision=runtime_profile.revision,
                managed_sections=runtime_profile_managed_sections(getattr(agent, "runtime_type", None)),
                config=config,
                source="portal.runtime_profile",
            )

    return AgentRuntimeContextResponse(
        agent_id=agent.id,
        agent_type=agent.agent_type,
        runtime_profile_id=agent.runtime_profile_id,
        runtime_profile_context=runtime_profile_context,
        runtime_target=RuntimeTargetInfoResponse(
            agent_id=agent.id,
            namespace=agent.namespace,
            service_name=agent.service_name,
            endpoint_path=agent.endpoint_path,
        ),
    )
