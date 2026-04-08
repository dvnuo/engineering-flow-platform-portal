from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.repositories.agent_repo import AgentRepository
from app.repositories.external_event_subscription_repo import ExternalEventSubscriptionRepository
from app.schemas.external_event_subscription import ExternalEventSubscriptionCreateRequest, ExternalEventSubscriptionResponse

router = APIRouter(tags=["external-event-subscriptions"])


def _can_write(agent, user) -> bool:
    return user.role == "admin" or agent.owner_user_id == user.id


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

    subscription = ExternalEventSubscriptionRepository(db).create(**payload.model_dump())
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
