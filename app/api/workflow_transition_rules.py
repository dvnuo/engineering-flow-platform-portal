from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.repositories.agent_repo import AgentRepository
from app.repositories.workflow_transition_rule_repo import WorkflowTransitionRuleRepository
from app.schemas.workflow_transition_rule import WorkflowTransitionRuleCreateRequest, WorkflowTransitionRuleResponse

router = APIRouter(prefix="/api/workflow-transition-rules", tags=["workflow-transition-rules"])


@router.post("", response_model=WorkflowTransitionRuleResponse)
def create_workflow_transition_rule(
    payload: WorkflowTransitionRuleCreateRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ = user
    target_agent = AgentRepository(db).get_by_id(payload.target_agent_id)
    if not target_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target agent not found")

    rule = WorkflowTransitionRuleRepository(db).create(**payload.model_dump())
    return WorkflowTransitionRuleResponse.model_validate(rule)


@router.get("", response_model=list[WorkflowTransitionRuleResponse])
def list_workflow_transition_rules(user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    rules = WorkflowTransitionRuleRepository(db).list_all()
    return [WorkflowTransitionRuleResponse.model_validate(rule) for rule in rules]


@router.get("/{rule_id}", response_model=WorkflowTransitionRuleResponse)
def get_workflow_transition_rule(rule_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    rule = WorkflowTransitionRuleRepository(db).get_by_id(rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WorkflowTransitionRule not found")
    return WorkflowTransitionRuleResponse.model_validate(rule)
