from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, require_internal_api_key
from app.schemas.agent_delegation import (
    AgentDelegationBoardItemResponse,
    AgentDelegationCreateRequest,
    InternalAgentDelegationCreateRequest,
    AgentDelegationResponse,
    AgentGroupTaskBoardResponse,
)
from app.schemas.group_shared_context_snapshot import (
    GroupSharedContextSnapshotDetailResponse,
    GroupSharedContextSnapshotResponse,
)
from app.services.agent_delegation_service import AgentDelegationService, AgentDelegationServiceError
from app.services.agent_group_service import AgentGroupService, AgentGroupServiceError

router = APIRouter(tags=["agent-delegations"])


def _raise_delegation_error(error: AgentDelegationServiceError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.detail)


def _raise_group_error(error: AgentGroupServiceError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.detail)


@router.post("/api/agent-delegations", response_model=AgentDelegationResponse)
async def create_agent_delegation(payload: AgentDelegationCreateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = AgentDelegationService(db)
    try:
        delegation = await service.create_delegation(payload, user=user)
    except AgentDelegationServiceError as error:
        _raise_delegation_error(error)
    return AgentDelegationResponse.model_validate(delegation)


@router.post("/api/internal/agent-delegations", response_model=AgentDelegationResponse)
async def create_internal_agent_delegation(
    payload: InternalAgentDelegationCreateRequest,
    _: bool = Depends(require_internal_api_key),
    db: Session = Depends(get_db),
):
    service = AgentDelegationService(db)
    try:
        delegation = await service.create_delegation_from_internal_request(payload)
    except AgentDelegationServiceError as error:
        _raise_delegation_error(error)
    return AgentDelegationResponse.model_validate(delegation)


@router.get("/api/agent-delegations/{delegation_id}", response_model=AgentDelegationResponse)
def get_agent_delegation(delegation_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = AgentDelegationService(db)
    try:
        delegation = service.get_delegation(delegation_id, user=user)
    except AgentDelegationServiceError as error:
        _raise_delegation_error(error)
    return AgentDelegationResponse.model_validate(delegation)


@router.get("/api/agent-groups/{group_id}/delegations", response_model=list[AgentDelegationResponse])
def list_group_delegations(group_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = AgentDelegationService(db)
    try:
        delegations = service.list_group_delegations(group_id, user=user, apply_visibility=True)
    except AgentDelegationServiceError as error:
        _raise_delegation_error(error)
    return [AgentDelegationResponse.model_validate(item) for item in delegations]


@router.get("/api/agent-groups/{group_id}/task-board", response_model=AgentGroupTaskBoardResponse)
def get_group_task_board(group_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = AgentGroupService(db)
    try:
        board = service.get_group_task_board(group_id, user=user, apply_visibility=True)
    except AgentGroupServiceError as error:
        _raise_group_error(error)

    return AgentGroupTaskBoardResponse(
        group_id=board["group_id"],
        leader_agent_id=board["leader_agent_id"],
        summary=board["summary"],
        items=[AgentDelegationBoardItemResponse.model_validate(item) for item in board["items"]],
    )


@router.get("/api/agent-groups/{group_id}/shared-contexts", response_model=list[GroupSharedContextSnapshotResponse])
def list_group_shared_context_snapshots(group_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = AgentGroupService(db)
    try:
        snapshots = service.list_group_shared_context_snapshots(group_id, user=user)
    except AgentGroupServiceError as error:
        _raise_group_error(error)
    return [GroupSharedContextSnapshotResponse.model_validate(item) for item in snapshots]


@router.get("/api/agent-groups/{group_id}/shared-contexts/{context_ref}", response_model=GroupSharedContextSnapshotDetailResponse)
def get_group_shared_context_snapshot(group_id: str, context_ref: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = AgentGroupService(db)
    try:
        snapshot = service.get_group_shared_context_snapshot(group_id, context_ref, user=user)
    except AgentGroupServiceError as error:
        _raise_group_error(error)
    return GroupSharedContextSnapshotDetailResponse.model_validate(snapshot)
