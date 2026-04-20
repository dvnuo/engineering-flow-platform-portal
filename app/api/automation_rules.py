from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime
import json

from app.db import get_db
from app.deps import get_current_user
from app.repositories.agent_repo import AgentRepository
from app.repositories.automation_rule_repo import AutomationRuleRepository
from app.schemas.automation_rule import (
    AutomationRuleCreate,
    AutomationRuleEventRead,
    AutomationRuleRead,
    AutomationRuleRunOnceResponse,
    AutomationRuleRunRead,
    AutomationRuleUpdate,
)
from app.services.automation_rule_service import AutomationRuleService

router = APIRouter(prefix="/api/automation-rules", tags=["automation-rules"])


def _can_write(agent, user) -> bool:
    return user.role == "admin" or agent.owner_user_id == user.id


def _ensure_accessible_target_agent(db: Session, user, target_agent_id: str):
    agent = AgentRepository(db).get_by_id(target_agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target agent not found")
    if not _can_write(agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return agent


@router.get("", response_model=list[AutomationRuleRead])
def list_automation_rules(user=Depends(get_current_user), db: Session = Depends(get_db)):
    items = AutomationRuleRepository(db).list(limit=200)
    if user.role != "admin":
        items = [item for item in items if item.owner_user_id == user.id]
    return [AutomationRuleRead.model_validate(item) for item in items]


@router.post("", response_model=AutomationRuleRead)
def create_automation_rule(payload: AutomationRuleCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_accessible_target_agent(db, user, payload.target_agent_id)
    rule = AutomationRuleService(db).create_rule(payload, current_user_id=user.id)
    return AutomationRuleRead.model_validate(rule)


@router.get("/{rule_id}", response_model=AutomationRuleRead)
def get_automation_rule(rule_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    rule = AutomationRuleRepository(db).get(rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AutomationRule not found")
    _ensure_accessible_target_agent(db, user, rule.target_agent_id)
    return AutomationRuleRead.model_validate(rule)


@router.patch("/{rule_id}", response_model=AutomationRuleRead)
def update_automation_rule(rule_id: str, payload: AutomationRuleUpdate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo = AutomationRuleRepository(db)
    rule = repo.get(rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AutomationRule not found")
    _ensure_accessible_target_agent(db, user, rule.target_agent_id)
    if payload.target_agent_id:
        _ensure_accessible_target_agent(db, user, payload.target_agent_id)
    updated = AutomationRuleService(db).update_rule(rule, payload, current_user_id=user.id)
    return AutomationRuleRead.model_validate(updated)


@router.delete("/{rule_id}")
def delete_automation_rule(rule_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo = AutomationRuleRepository(db)
    rule = repo.get(rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AutomationRule not found")
    _ensure_accessible_target_agent(db, user, rule.target_agent_id)
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


@router.post("/{rule_id}/run-once", response_model=AutomationRuleRunOnceResponse)
async def run_automation_rule_once(rule_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    rule = AutomationRuleRepository(db).get(rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AutomationRule not found")
    _ensure_accessible_target_agent(db, user, rule.target_agent_id)
    result = await AutomationRuleService(db).run_rule_once(rule_id, triggered_by="api")
    return AutomationRuleRunOnceResponse(**result.__dict__)


@router.get("/{rule_id}/runs", response_model=list[AutomationRuleRunRead])
def list_rule_runs(rule_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    rule = AutomationRuleRepository(db).get(rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AutomationRule not found")
    _ensure_accessible_target_agent(db, user, rule.target_agent_id)
    return [AutomationRuleRunRead.model_validate(run) for run in AutomationRuleRepository(db).list_runs(rule_id, limit=50)]


@router.get("/{rule_id}/events", response_model=list[AutomationRuleEventRead])
def list_rule_events(rule_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    rule = AutomationRuleRepository(db).get(rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AutomationRule not found")
    _ensure_accessible_target_agent(db, user, rule.target_agent_id)
    return [AutomationRuleEventRead.model_validate(event) for event in AutomationRuleRepository(db).list_events(rule_id, limit=100)]
