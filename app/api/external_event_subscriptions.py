from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.repositories.agent_identity_binding_repo import AgentIdentityBindingRepository
from app.repositories.agent_repo import AgentRepository
from app.repositories.external_event_subscription_repo import ExternalEventSubscriptionRepository
from app.schemas.external_event_subscription import ExternalEventSubscriptionCreateRequest, ExternalEventSubscriptionResponse

router = APIRouter(tags=["external-event-subscriptions"])


def _can_write(agent, user) -> bool:
    return user.role == "admin" or agent.owner_user_id == user.id


def _derive_source_kind(source_type: str, event_type: str, provided_source_kind: str | None) -> str:
    if provided_source_kind and provided_source_kind.strip():
        return provided_source_kind.strip()
    return f"{(source_type or '').strip().lower()}.{(event_type or '').strip()}"


def _normalize_mode(mode: str | None) -> str:
    cleaned = (mode or "").strip().lower()
    return cleaned or "push"


@router.post("/api/external-event-subscriptions", response_model=ExternalEventSubscriptionResponse)
def create_external_event_subscription(
    payload: ExternalEventSubscriptionCreateRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agent = AgentRepository(db).get_by_id(payload.agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not _can_write(agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    create_payload = payload.model_dump()
    normalized_source_type = (payload.source_type or "").strip().lower()
    normalized_mode = _normalize_mode(payload.mode)
    if normalized_mode not in {"push", "poll", "hybrid"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="mode must be one of push, poll, hybrid")

    if payload.binding_id:
        binding = AgentIdentityBindingRepository(db).get_by_id(payload.binding_id)
        if not binding or binding.agent_id != payload.agent_id or binding.system_type != normalized_source_type:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="binding_id must refer to a binding on the same agent and provider",
            )

    create_payload["source_type"] = normalized_source_type
    create_payload["mode"] = normalized_mode
    create_payload["source_kind"] = _derive_source_kind(
        normalized_source_type,
        payload.event_type,
        payload.source_kind,
    )
    subscription = ExternalEventSubscriptionRepository(db).create(**create_payload)
    return ExternalEventSubscriptionResponse.model_validate(subscription)


@router.get("/api/external-event-subscriptions", response_model=list[ExternalEventSubscriptionResponse])
def list_external_event_subscriptions(user=Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    subscriptions = ExternalEventSubscriptionRepository(db).list_all()
    return [ExternalEventSubscriptionResponse.model_validate(item) for item in subscriptions]


@router.get("/api/agents/{agent_id}/external-event-subscriptions", response_model=list[ExternalEventSubscriptionResponse])
def list_external_event_subscriptions_by_agent(agent_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not _can_write(agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    subscriptions = ExternalEventSubscriptionRepository(db).list_by_agent(agent_id)
    return [ExternalEventSubscriptionResponse.model_validate(item) for item in subscriptions]
