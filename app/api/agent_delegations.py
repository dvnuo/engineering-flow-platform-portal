from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.schemas.agent_delegation import (
    AgentDelegationBoardItemResponse,
    AgentDelegationCreateRequest,
    AgentDelegationResponse,
    AgentGroupTaskBoardResponse,
)
from app.services.agent_delegation_service import AgentDelegationService, AgentDelegationServiceError
from app.services.agent_group_service import AgentGroupService, AgentGroupServiceError

router = APIRouter(tags=["agent-delegations"])


def _raise_delegation_error(error: AgentDelegationServiceError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.detail)


def _raise_group_error(error: AgentGroupServiceError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.detail)


@router.post("/api/agent-delegations", response_model=AgentDelegationResponse)
def create_agent_delegation(payload: AgentDelegationCreateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    service = AgentDelegationService(db)
    try:
        delegation = service.create_delegation(payload)
    except AgentDelegationServiceError as error:
        _raise_delegation_error(error)
    return AgentDelegationResponse.model_validate(delegation)


@router.get("/api/agent-delegations/{delegation_id}", response_model=AgentDelegationResponse)
def get_agent_delegation(delegation_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    service = AgentDelegationService(db)
    try:
        delegation = service.get_delegation(delegation_id)
    except AgentDelegationServiceError as error:
        _raise_delegation_error(error)
    return AgentDelegationResponse.model_validate(delegation)


@router.get("/api/agent-groups/{group_id}/delegations", response_model=list[AgentDelegationResponse])
def list_group_delegations(group_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    service = AgentDelegationService(db)
    try:
        delegations = service.list_group_delegations(group_id)
    except AgentDelegationServiceError as error:
        _raise_delegation_error(error)
    return [AgentDelegationResponse.model_validate(item) for item in delegations]


@router.get("/api/agent-groups/{group_id}/task-board", response_model=AgentGroupTaskBoardResponse)
def get_group_task_board(group_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    service = AgentGroupService(db)
    try:
        board = service.get_group_task_board(group_id)
    except AgentGroupServiceError as error:
        _raise_group_error(error)

    return AgentGroupTaskBoardResponse(
        group_id=board["group_id"],
        leader_agent_id=board["leader_agent_id"],
        summary=board["summary"],
        items=[AgentDelegationBoardItemResponse.model_validate(item) for item in board["items"]],
    )
