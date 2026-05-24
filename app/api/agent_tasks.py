from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
import json
import logging
from uuid import uuid4

from app.db import get_db
from app.deps import get_current_user
from app.repositories.agent_group_repo import AgentGroupRepository
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.schemas.agent_task import (
    AgentTaskCreateRequest,
    AgentTaskResponse,
    CreateAgentAsyncTaskRequest,
    CreateAgentTaskFollowupRequest,
    CreateTaskFromTemplateRequest,
    TaskTemplateRead,
)
from app.services.agent_group_service import AgentGroupService, AgentGroupServiceError
from app.services.task_dispatcher import TaskDispatcherService
from app.services.task_template_registry import build_agent_task_create_payload_from_template, list_task_templates, require_task_template

router = APIRouter(tags=["agent-tasks"])
task_dispatcher_service = TaskDispatcherService()
logger = logging.getLogger(__name__)
AGENT_ASYNC_TASK_TYPE = "agent_async_task"
AGENT_ASYNC_TASK_FAMILY = "agent_task"
AGENT_ASYNC_TASK_AUTONOMOUS_INSTRUCTION = (
    "Run as a background long-running task. Do not ask the user for more information unless truly blocked. "
    "Make reasonable assumptions and complete as much as possible."
)
ACTIVE_TASK_STATUSES = {"queued", "running"}
TERMINAL_TASK_STATUSES = {"done", "failed", "blocked", "stale", "cancelled", "pending_restart", "cancel_failed"}


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


def _require_writable_assignee(db: Session, assignee_agent_id: str, user):
    cleaned_agent_id = (assignee_agent_id or "").strip()
    if not cleaned_agent_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="assignee_agent_id is required")
    assignee_agent = AgentRepository(db).get_by_id(cleaned_agent_id)
    if not assignee_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignee agent not found")
    if not _can_write(assignee_agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return assignee_agent


def _require_visible_task(db: Session, task_id: str, user):
    task = AgentTaskRepository(db).get_by_id(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if user.role != "admin":
        visible_group_ids = _visible_group_ids_for_user(db, user)
        if not _is_task_visible_to_user(task, user, visible_group_ids):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


def _can_manage_task(db: Session, task, user) -> bool:
    if user.role == "admin":
        return True
    if task.owner_user_id == user.id or task.created_by_user_id == user.id:
        return True
    if task.group_id:
        group_service = AgentGroupService(db)
        group = group_service.group_repo.get_by_id(task.group_id)
        return bool(group and group_service.can_manage_group_tasks(group, user))
    assignee_agent = AgentRepository(db).get_by_id(task.assignee_agent_id)
    return bool(assignee_agent and _can_write(assignee_agent, user))


def _normalize_skill_name(value: str | None) -> str:
    return (value or "").strip().lstrip("/").strip()


def _derive_task_title(content: str) -> str:
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
    title = first_line or content.strip()
    title = " ".join(title.split())
    if len(title) > 96:
        return title[:93].rstrip() + "..."
    return title or "Agent background task"


def _agent_async_payload(
    *,
    task_content: str,
    skill_name: str,
    task_session_id: str,
    root_task_id: str,
    parent_task_id: str | None,
    previous_task_id: str | None = None,
) -> dict:
    payload = {
        "schema": "agent_async_task.v1",
        "skill_name": skill_name,
        "task_session_id": task_session_id,
        "root_task_id": root_task_id,
        "parent_task_id": parent_task_id,
        "autonomous": True,
        "autonomous_instruction": AGENT_ASYNC_TASK_AUTONOMOUS_INSTRUCTION,
    }
    if previous_task_id:
        payload["followup_task"] = task_content
        payload["previous_task_id"] = previous_task_id
    else:
        payload["user_task"] = task_content
    return payload


def _root_id_for_task(task) -> str:
    return (getattr(task, "root_task_id", None) or getattr(task, "id", "") or "").strip()


def _chain_has_active_task(db: Session, root_task_id: str) -> bool:
    if not root_task_id:
        return False
    for item in AgentTaskRepository(db).list_by_root_task_id(root_task_id):
        if (item.status or "").strip().lower() in ACTIVE_TASK_STATUSES:
            return True
    return False


@router.get("/api/task-templates", response_model=list[TaskTemplateRead])
def list_templates(user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    _ = db
    return [TaskTemplateRead(**template.__dict__) for template in list_task_templates()]


@router.post("/api/agent-tasks/from-template", response_model=AgentTaskResponse)
def create_agent_task_from_template(payload: CreateTaskFromTemplateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    assignee_agent = AgentRepository(db).get_by_id(payload.assignee_agent_id)
    if not assignee_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignee agent not found")
    if not _can_write(assignee_agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    try:
        require_task_template(payload.template_id)
        task_payload = build_agent_task_create_payload_from_template(
            payload.template_id,
            payload.input,
            payload.assignee_agent_id,
            current_user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if payload.parent_agent_id is not None:
        parent_agent = AgentRepository(db).get_by_id(payload.parent_agent_id)
        if not parent_agent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent agent not found")

    task = AgentTaskRepository(db).create(
        group_id=payload.group_id,
        parent_agent_id=payload.parent_agent_id,
        assignee_agent_id=payload.assignee_agent_id,
        owner_user_id=assignee_agent.owner_user_id,
        created_by_user_id=user.id,
        source=task_payload["source"],
        task_type=task_payload["task_type"],
        template_id=task_payload["template_id"],
        input_payload_json=json.dumps(task_payload["input_payload_json"]),
        task_family=task_payload.get("task_family"),
        provider=task_payload.get("provider"),
        trigger=task_payload.get("trigger"),
        status="queued",
    )
    if payload.dispatch_immediately:
        task_dispatcher_service.dispatch_task_in_background(task.id)
    return AgentTaskResponse.model_validate(task)


@router.post("/api/agent-tasks/async", response_model=AgentTaskResponse)
def create_agent_async_task(payload: CreateAgentAsyncTaskRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    task_content = (payload.task_content or "").strip()
    skill_name = _normalize_skill_name(payload.skill_name)
    if not skill_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="skill_name is required")
    if not task_content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="task_content is required")

    assignee_agent = _require_writable_assignee(db, payload.assignee_agent_id, user)
    task_id = str(uuid4())
    task_session_id = f"agent-task:{task_id}"
    task_input = _agent_async_payload(
        task_content=task_content,
        skill_name=skill_name,
        task_session_id=task_session_id,
        root_task_id=task_id,
        parent_task_id=None,
    )
    task = AgentTaskRepository(db).create(
        id=task_id,
        assignee_agent_id=assignee_agent.id,
        owner_user_id=assignee_agent.owner_user_id,
        created_by_user_id=user.id,
        source="portal",
        task_type=AGENT_ASYNC_TASK_TYPE,
        task_family=AGENT_ASYNC_TASK_FAMILY,
        template_id=None,
        provider=None,
        trigger="manual",
        title=_derive_task_title(task_content),
        skill_name=skill_name,
        parent_task_id=None,
        root_task_id=task_id,
        task_session_id=task_session_id,
        input_payload_json=json.dumps(task_input),
        status="queued",
    )
    task_dispatcher_service.dispatch_task_in_background(task.id)
    return AgentTaskResponse.model_validate(task)


@router.post("/api/agent-tasks/{task_id}/followups", response_model=AgentTaskResponse)
def create_agent_task_followup(
    task_id: str,
    payload: CreateAgentTaskFollowupRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task_content = (payload.task_content or "").strip()
    if not task_content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="task_content is required")

    target_task = _require_visible_task(db, task_id, user)
    if target_task.task_type != AGENT_ASYNC_TASK_TYPE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Follow-up is only supported for agent async tasks")
    if (target_task.status or "").strip().lower() in ACTIVE_TASK_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task is still running")

    root_task_id = _root_id_for_task(target_task)
    if _chain_has_active_task(db, root_task_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task chain is still running")
    skill_name = _normalize_skill_name(getattr(target_task, "skill_name", None))
    if not skill_name:
        input_payload = {}
        try:
            parsed = json.loads(target_task.input_payload_json or "{}")
            input_payload = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            input_payload = {}
        skill_name = _normalize_skill_name(input_payload.get("skill_name"))
    if not skill_name:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task is missing a selected skill")

    task_session_id = (target_task.task_session_id or f"agent-task:{root_task_id}").strip()
    child_task_id = str(uuid4())
    task_input = _agent_async_payload(
        task_content=task_content,
        skill_name=skill_name,
        task_session_id=task_session_id,
        root_task_id=root_task_id,
        parent_task_id=target_task.id,
        previous_task_id=target_task.id,
    )
    child_task = AgentTaskRepository(db).create(
        id=child_task_id,
        assignee_agent_id=target_task.assignee_agent_id,
        owner_user_id=target_task.owner_user_id,
        created_by_user_id=user.id,
        source="portal",
        task_type=AGENT_ASYNC_TASK_TYPE,
        task_family=AGENT_ASYNC_TASK_FAMILY,
        template_id=None,
        provider=None,
        trigger="manual",
        title=_derive_task_title(task_content),
        skill_name=skill_name,
        parent_task_id=target_task.id,
        root_task_id=root_task_id,
        task_session_id=task_session_id,
        input_payload_json=json.dumps(task_input),
        status="queued",
    )
    task_dispatcher_service.dispatch_task_in_background(child_task.id)
    return AgentTaskResponse.model_validate(child_task)


@router.post("/api/agent-tasks/{task_id}/cancel", response_model=AgentTaskResponse)
async def cancel_agent_task(task_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    task = _require_visible_task(db, task_id, user)
    if not _can_manage_task(db, task, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to cancel task")

    normalized_status = (task.status or "").strip().lower()
    if normalized_status in TERMINAL_TASK_STATUSES:
        return AgentTaskResponse.model_validate(task)
    if normalized_status == "queued":
        task.status = "cancelled"
        task.summary = "Task was cancelled before it started."
        task = AgentTaskRepository(db).save(task)
        return AgentTaskResponse.model_validate(task)
    if normalized_status != "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task is not cancellable")

    try:
        task = await task_dispatcher_service.cancel_task(task.id, db, user=user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return AgentTaskResponse.model_validate(task)
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
