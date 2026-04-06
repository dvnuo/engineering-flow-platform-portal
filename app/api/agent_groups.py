from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.repositories.agent_group_repo import AgentGroupRepository
from app.schemas.agent_group import (
    AgentGroupCreateRequest,
    AgentGroupDetailResponse,
    AgentGroupMemberCreateRequest,
    AgentGroupMemberResponse,
    AgentGroupResponse,
    AgentGroupTaskCreateRequest,
    AgentGroupTaskSummaryResponse,
)
from app.schemas.agent_task import AgentTaskResponse
from app.services.agent_group_service import AgentGroupService, AgentGroupServiceError

router = APIRouter(tags=["agent-groups"])


def _raise_http_service_error(error: AgentGroupServiceError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.detail)


@router.post("/api/agent-groups", response_model=AgentGroupDetailResponse)
def create_agent_group(payload: AgentGroupCreateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = AgentGroupService(db)
    try:
        group, members = service.create_group_with_members(payload, created_by_user_id=user.id)
    except AgentGroupServiceError as error:
        _raise_http_service_error(error)

    return AgentGroupDetailResponse(
        **AgentGroupResponse.model_validate(group).model_dump(),
        members=[AgentGroupMemberResponse.model_validate(item) for item in members],
    )


@router.get("/api/agent-groups", response_model=list[AgentGroupResponse])
def list_agent_groups(user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    groups = AgentGroupRepository(db).list_all()
    return [AgentGroupResponse.model_validate(group) for group in groups]


@router.get("/api/agent-groups/{group_id}", response_model=AgentGroupDetailResponse)
def get_agent_group(group_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    service = AgentGroupService(db)
    group = service.group_repo.get_by_id(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    members = service.member_repo.list_by_group(group.id)
    return AgentGroupDetailResponse(
        **AgentGroupResponse.model_validate(group).model_dump(),
        members=[AgentGroupMemberResponse.model_validate(item) for item in members],
    )


@router.post("/api/agent-groups/{group_id}/members", response_model=AgentGroupMemberResponse)
def add_agent_group_member(
    group_id: str,
    payload: AgentGroupMemberCreateRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ = user
    service = AgentGroupService(db)
    try:
        member = service.add_group_member(group_id, payload)
    except AgentGroupServiceError as error:
        _raise_http_service_error(error)
    return AgentGroupMemberResponse.model_validate(member)


@router.delete("/api/agent-groups/{group_id}/members/{member_id}")
def delete_agent_group_member(group_id: str, member_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    service = AgentGroupService(db)
    try:
        service.remove_group_member(group_id, member_id)
    except AgentGroupServiceError as error:
        _raise_http_service_error(error)
    return {"ok": True}


@router.get("/api/agent-groups/{group_id}/tasks", response_model=list[AgentTaskResponse])
def list_group_tasks(group_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    service = AgentGroupService(db)
    try:
        tasks = service.list_group_tasks(group_id)
    except AgentGroupServiceError as error:
        _raise_http_service_error(error)
    return [AgentTaskResponse.model_validate(task) for task in tasks]


@router.get("/api/agent-groups/{group_id}/task-summary", response_model=AgentGroupTaskSummaryResponse)
def get_group_task_summary(group_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    service = AgentGroupService(db)
    try:
        summary = service.get_group_task_summary(group_id)
    except AgentGroupServiceError as error:
        _raise_http_service_error(error)
    return summary


@router.post("/api/agent-groups/{group_id}/tasks", response_model=AgentTaskResponse)
def create_group_scoped_task(
    group_id: str,
    payload: AgentGroupTaskCreateRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ = user
    service = AgentGroupService(db)
    try:
        task = service.create_group_task(group_id, payload)
    except AgentGroupServiceError as error:
        _raise_http_service_error(error)
    return AgentTaskResponse.model_validate(task)
