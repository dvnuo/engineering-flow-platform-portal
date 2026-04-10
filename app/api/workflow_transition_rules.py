from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.repositories.agent_repo import AgentRepository
from app.repositories.workflow_transition_rule_repo import WorkflowTransitionRuleRepository
from app.schemas.workflow_transition_rule import WorkflowTransitionRuleCreateRequest, WorkflowTransitionRuleResponse
from app.services.workflow_rule_config import parse_workflow_rule_config

router = APIRouter(prefix="/api/workflow-transition-rules", tags=["workflow-transition-rules"])


def _can_write(agent, user) -> bool:
    return user.role == "admin" or agent.owner_user_id == user.id


@router.post("", response_model=WorkflowTransitionRuleResponse)
def create_workflow_transition_rule(
    payload: WorkflowTransitionRuleCreateRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target_agent = AgentRepository(db).get_by_id(payload.target_agent_id)
    if not target_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target agent not found")
    if not _can_write(target_agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    normalized_config_json, _parsed_config, config_error = parse_workflow_rule_config(payload.config_json)
    if config_error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=config_error)

    payload_data = payload.model_dump()
    payload_data["config_json"] = normalized_config_json

    rule = WorkflowTransitionRuleRepository(db).create(**payload_data)
    return WorkflowTransitionRuleResponse.model_validate(rule)


@router.get("", response_model=list[WorkflowTransitionRuleResponse])
def list_workflow_transition_rules(user=Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    rules = WorkflowTransitionRuleRepository(db).list_all()
    return [WorkflowTransitionRuleResponse.model_validate(rule) for rule in rules]


@router.get("/{rule_id}", response_model=WorkflowTransitionRuleResponse)
def get_workflow_transition_rule(rule_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    rule = WorkflowTransitionRuleRepository(db).get_by_id(rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WorkflowTransitionRule not found")
    target_agent = AgentRepository(db).get_by_id(rule.target_agent_id)
    if not target_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target agent not found")
    if not _can_write(target_agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return WorkflowTransitionRuleResponse.model_validate(rule)
