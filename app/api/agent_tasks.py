from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
import logging

from app.db import get_db
from app.deps import get_current_user
from app.repositories.agent_group_repo import AgentGroupRepository
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.schemas.agent_task import AgentTaskCreateRequest, AgentTaskResponse
from app.services.agent_group_service import AgentGroupService, AgentGroupServiceError
from app.services.task_dispatcher import TaskDispatcherService

router = APIRouter(tags=["agent-tasks"])
task_dispatcher_service = TaskDispatcherService()
logger = logging.getLogger(__name__)


def _can_write(agent, user) -> bool:
    return user.role == "admin" or agent.owner_user_id == user.id


def _visible_group_ids_for_user(db: Session, user) -> list[str]:
    service = AgentGroupService(db)
    groups = AgentGroupRepository(db).list_all()
    return [group.id for group in groups if service.can_view_group(group, user)]


def _is_task_visible_to_user(task, user, visible_group_ids: list[str]) -> bool:
    if user.role == "admin":
        return True
    if task.owner_user_id == user.id or task.created_by_user_id == user.id:
        return True
    return bool(task.group_id and task.group_id in visible_group_ids)


@router.post("/api/agent-tasks", response_model=AgentTaskResponse)
def create_agent_task(payload: AgentTaskCreateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    group_service = AgentGroupService(db)

    if payload.group_id is not None:
        try:
            task = group_service.create_group_task(payload.group_id, payload, user=user)
        except AgentGroupServiceError as error:
            raise HTTPException(status_code=error.status_code, detail=error.detail)
        return AgentTaskResponse.model_validate(task)

    assignee_agent = AgentRepository(db).get_by_id(payload.assignee_agent_id)
    if not assignee_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignee agent not found")
    if not _can_write(assignee_agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    if payload.parent_agent_id is not None:
        parent_agent = AgentRepository(db).get_by_id(payload.parent_agent_id)
        if not parent_agent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent agent not found")
        if user.role != "admin" and parent_agent.owner_user_id != user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    create_payload = payload.model_dump()
    create_payload["owner_user_id"] = assignee_agent.owner_user_id
    create_payload["created_by_user_id"] = user.id
    task = AgentTaskRepository(db).create(**create_payload)
    return AgentTaskResponse.model_validate(task)


@router.get("/api/agent-tasks", response_model=list[AgentTaskResponse])
def list_agent_tasks(group_id: str | None = None, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo = AgentTaskRepository(db)
    if group_id:
        group_service = AgentGroupService(db)
        group = group_service.group_repo.get_by_id(group_id)
        if not group:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        if not group_service.can_view_group(group, user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to view group tasks")
        tasks = group_service.list_group_tasks(group_id, user=user)
    else:
        if user.role != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admin can list all tasks")
        tasks = repo.list_all()
    return [AgentTaskResponse.model_validate(task) for task in tasks]


@router.get("/api/my/tasks", response_model=list[AgentTaskResponse])
def list_my_tasks(user=Depends(get_current_user), db: Session = Depends(get_db)):
    visible_group_ids = _visible_group_ids_for_user(db, user)
    tasks = AgentTaskRepository(db).list_visible_to_user(user_id=user.id, visible_group_ids=visible_group_ids)
    return [AgentTaskResponse.model_validate(task) for task in tasks]


@router.get("/api/agent-tasks/{task_id}", response_model=AgentTaskResponse)
def get_agent_task(task_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    task = AgentTaskRepository(db).get_by_id(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if user.role != "admin":
        visible_group_ids = _visible_group_ids_for_user(db, user)
        if not _is_task_visible_to_user(task, user, visible_group_ids):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    return AgentTaskResponse.model_validate(task)


@router.get("/api/agents/{agent_id}/tasks", response_model=list[AgentTaskResponse])
def list_agent_tasks_by_agent(agent_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if user.role != "admin" and agent.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    tasks = AgentTaskRepository(db).list_by_agent(agent_id)
    return [AgentTaskResponse.model_validate(task) for task in tasks]


@router.post("/api/agent-tasks/{task_id}/dispatch", status_code=status.HTTP_202_ACCEPTED)
async def dispatch_agent_task(task_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    task = AgentTaskRepository(db).get_by_id(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if task.group_id:
        group_service = AgentGroupService(db)
        group = group_service.group_repo.get_by_id(task.group_id)
        if not group:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        if not group_service.can_manage_group_tasks(group, user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to dispatch group task")
    else:
        assignee_agent = AgentRepository(db).get_by_id(task.assignee_agent_id)
        if not assignee_agent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignee agent not found")
        if user.role != "admin" and assignee_agent.owner_user_id != user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to dispatch task")

    if task.status != "queued":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task is not dispatchable")

    logger.info(
        "Manual task dispatch scheduled task_id=%s task_type=%s assignee_agent_id=%s group_id=%s",
        task.id,
        task.task_type,
        task.assignee_agent_id,
        task.group_id or "-",
    )
    task_dispatcher_service.dispatch_task_in_background(task_id=task_id)
    return {
        "accepted": True,
        "task_id": task.id,
        "task_status": task.status,
        "message": "Task scheduled for background dispatch",
    }
