from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime
import json

from app.db import get_db
from app.deps import get_current_user
from app.repositories.agent_repo import AgentRepository
from app.repositories.delegation_rule_repo import DelegationRuleRepository
from app.repositories.user_repo import UserRepository
from app.schemas.delegation_rule import (
    DelegationRuleCreate,
    DelegationRuleEventRead,
    DelegationRuleRead,
    DelegationRuleRunOnceResponse,
    DelegationRuleRunRead,
    DelegationRuleUpdate,
)
from app.services.delegation_rule_service import DelegationRuleService

router = APIRouter(prefix="/api/delegation-rules", tags=["delegation-rules"])


def _can_write(agent, user) -> bool:
    return user.role == "admin" or agent.owner_user_id == user.id


def _can_manage_rule(rule, user) -> bool:
    return getattr(rule, "owner_user_id", None) == getattr(user, "id", None)


def _require_manage_rule(rule, user) -> None:
    if not _can_manage_rule(rule, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner can manage this delegation")


def _user_display_name(db: Session, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    owner = UserRepository(db).get_by_id(user_id)
    if not owner:
        return f"User {user_id}"
    return (owner.nickname or owner.username or f"User {user_id}").strip()


def _delegation_rule_response(db: Session, rule, user) -> DelegationRuleRead:
    agent = AgentRepository(db).get_by_id(rule.target_agent_id)
    agent_name = (getattr(agent, "name", None) or "").strip() if agent else None
    return DelegationRuleRead.model_validate(rule).model_copy(
        update={
            "owner_display_name": _user_display_name(db, getattr(rule, "owner_user_id", None)),
            "can_manage": _can_manage_rule(rule, user),
            "target_agent_name": agent_name or None,
            "target_agent_missing": agent is None,
        }
    )


def _ensure_accessible_target_agent(db: Session, user, target_agent_id: str):
    agent = AgentRepository(db).get_by_id(target_agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target agent not found")
    if not _can_write(agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return agent


@router.get("", response_model=list[DelegationRuleRead])
def list_delegation_rules(user=Depends(get_current_user), db: Session = Depends(get_db)):
    items = DelegationRuleRepository(db).list(limit=200)
    return [_delegation_rule_response(db, item, user) for item in items]


@router.post("", response_model=DelegationRuleRead)
def create_delegation_rule(payload: DelegationRuleCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_accessible_target_agent(db, user, payload.target_agent_id)
    rule = DelegationRuleService(db).create_rule(payload, current_user_id=user.id)
    return _delegation_rule_response(db, rule, user)


@router.get("/{rule_id}", response_model=DelegationRuleRead)
def get_delegation_rule(rule_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    rule = DelegationRuleRepository(db).get(rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DelegationRule not found")
    return _delegation_rule_response(db, rule, user)


@router.patch("/{rule_id}", response_model=DelegationRuleRead)
def update_delegation_rule(rule_id: str, payload: DelegationRuleUpdate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo = DelegationRuleRepository(db)
    rule = repo.get(rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DelegationRule not found")
    if repo.is_deleted_rule(rule):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="DelegationRule is archived")
    _require_manage_rule(rule, user)
    if payload.target_agent_id:
        _ensure_accessible_target_agent(db, user, payload.target_agent_id)
    updated = DelegationRuleService(db).update_rule(rule, payload, current_user_id=user.id)
    return _delegation_rule_response(db, updated, user)


@router.delete("/{rule_id}")
def delete_delegation_rule(rule_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo = DelegationRuleRepository(db)
    rule = repo.get(rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DelegationRule not found")
    _require_manage_rule(rule, user)
    if repo.is_deleted_rule(rule):
        return {"ok": True}
    now = datetime.utcnow()
    try:
        state_obj = json.loads(rule.state_json or "{}")
    except Exception:
        state_obj = {}
    state_obj.update(
        {
            "deleted": True,
            "deleted_at": now.isoformat(),
            "deleted_by_user_id": user.id,
        }
    )
    repo.update(
        rule,
        {
            "enabled": False,
            "next_run_at": None,
            "locked_until": None,
            "state_json": json.dumps(state_obj),
            "updated_at": now,
        },
    )
    return {"ok": True}


@router.post("/{rule_id}/run-once", response_model=DelegationRuleRunOnceResponse)
async def run_delegation_rule_once(rule_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    rule = DelegationRuleRepository(db).get(rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DelegationRule not found")
    if DelegationRuleRepository(db).is_deleted_rule(rule):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="DelegationRule is archived")
    _require_manage_rule(rule, user)
    result = await DelegationRuleService(db).run_rule_once(rule_id, triggered_by="api")
    return DelegationRuleRunOnceResponse(**result.__dict__)


@router.get("/{rule_id}/runs", response_model=list[DelegationRuleRunRead])
def list_rule_runs(rule_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    rule = DelegationRuleRepository(db).get(rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DelegationRule not found")
    return [DelegationRuleRunRead.model_validate(run) for run in DelegationRuleRepository(db).list_runs(rule_id, limit=50)]


@router.get("/{rule_id}/events", response_model=list[DelegationRuleEventRead])
def list_rule_events(rule_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    rule = DelegationRuleRepository(db).get(rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DelegationRule not found")
    return [DelegationRuleEventRead.model_validate(event) for event in DelegationRuleRepository(db).list_events(rule_id, limit=100)]
