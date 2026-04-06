from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.repositories.agent_identity_binding_repo import AgentIdentityBindingRepository
from app.repositories.agent_repo import AgentRepository
from app.schemas.agent_identity_binding import AgentIdentityBindingCreateRequest, AgentIdentityBindingResponse

router = APIRouter(prefix="/api/agents", tags=["agent-identity-bindings"])
DUPLICATE_BINDING_DETAIL = "Identity binding already exists for this agent/system/account"


def _can_write(agent, user) -> bool:
    return user.role == "admin" or agent.owner_user_id == user.id


def _normalize_system_type(system_type: str) -> str:
    return (system_type or "").strip().lower()


@router.post("/{agent_id}/identity-bindings", response_model=AgentIdentityBindingResponse)
def create_identity_binding(
    agent_id: str,
    payload: AgentIdentityBindingCreateRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not _can_write(agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    repo = AgentIdentityBindingRepository(db)
    normalized_system_type = _normalize_system_type(payload.system_type)
    existing_binding = repo.get_by_agent_and_binding_key(
        agent_id=agent.id,
        system_type=normalized_system_type,
        external_account_id=payload.external_account_id,
        enabled_only=False,
    )
    if existing_binding:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=DUPLICATE_BINDING_DETAIL,
        )

    payload_data = payload.model_dump()
    payload_data["system_type"] = normalized_system_type
    try:
        binding = repo.create(agent_id=agent.id, **payload_data)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=DUPLICATE_BINDING_DETAIL,
        ) from exc

    return AgentIdentityBindingResponse.model_validate(binding)


@router.get("/{agent_id}/identity-bindings", response_model=list[AgentIdentityBindingResponse])
def list_identity_bindings(agent_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not _can_write(agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    bindings = AgentIdentityBindingRepository(db).list_by_agent(agent_id)
    return [AgentIdentityBindingResponse.model_validate(binding) for binding in bindings]


@router.delete("/{agent_id}/identity-bindings/{binding_id}")
def delete_identity_binding(agent_id: str, binding_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not _can_write(agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    repo = AgentIdentityBindingRepository(db)
    binding = repo.get_by_id(binding_id)
    if not binding or binding.agent_id != agent_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Binding not found")

    repo.delete(binding)
    return {"ok": True}
