from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
import json
import logging
from uuid import uuid4

from app.db import get_db
from app.deps import get_current_user
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.user_repo import UserRepository
from app.schemas.agent_task import (
    AgentTaskCreateRequest,
    AgentTaskListItemResponse,
    AgentTaskResponse,
    CreateAgentAsyncTaskRequest,
    CreateAgentTaskFollowupRequest,
)
from app.services.task_dispatcher import TaskDispatcherService
from app.services.agent_execution_registry import (
    mark_task_execution_status_best_effort,
    upsert_task_execution_queued_best_effort,
)

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


def _is_task_visible_to_user(task, user) -> bool:
    _ = task, user
    return True


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
        if not _is_task_visible_to_user(task, user):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


def _can_manage_task(db: Session, task, user) -> bool:
    _ = db
    return task.owner_user_id == user.id


def _user_display_name(db: Session, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    owner = UserRepository(db).get_by_id(user_id)
    if not owner:
        return f"User {user_id}"
    return (owner.nickname or owner.username or f"User {user_id}").strip()


def _task_response(db: Session, task, user) -> AgentTaskResponse:
    assignee_agent = AgentRepository(db).get_by_id(getattr(task, "assignee_agent_id", None))
    assignee_name = (getattr(assignee_agent, "name", None) or "").strip() if assignee_agent else None
    return AgentTaskResponse.model_validate(task).model_copy(
        update={
            "owner_display_name": _user_display_name(db, getattr(task, "owner_user_id", None)),
            "can_manage": _can_manage_task(db, task, user),
            "assignee_agent_name": assignee_name or None,
        }
    )


def _compact_display_title(value: str | None, *, fallback: str = "Task", limit: int = 80) -> str:
    cleaned = " ".join((value or "").strip().split())
    if not cleaned:
        cleaned = fallback
    if len(cleaned) > limit:
        return cleaned[: limit - 3].rstrip() + "..."
    return cleaned


def _owner_display_name_from_row(row: dict) -> str | None:
    owner_user_id = row.get("owner_user_id")
    if owner_user_id is None:
        return None
    owner_name = str(row.get("owner_nickname") or row.get("owner_username") or "").strip()
    return owner_name or f"User {owner_user_id}"


def _task_list_item_response(row: dict, user) -> AgentTaskListItemResponse:
    data = dict(row)
    data["owner_display_name"] = _owner_display_name_from_row(data)
    data["can_manage"] = data.get("owner_user_id") == getattr(user, "id", None)
    data["display_title"] = _compact_display_title(
        data.get("title"),
        fallback=str(data.get("task_type") or data.get("id") or "Task"),
    )
    data.pop("owner_username", None)
    data.pop("owner_nickname", None)
    return AgentTaskListItemResponse(**data)


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
    original_task: str | None = None,
    is_followup: bool = False,
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
    if is_followup or previous_task_id:
        payload["followup_task"] = task_content
        if previous_task_id:
            payload["previous_task_id"] = previous_task_id
        if original_task and original_task.strip():
            payload["original_task"] = original_task.strip()
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


def _input_payload_for_task(task) -> dict:
    try:
        parsed = json.loads(getattr(task, "input_payload_json", None) or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _skill_name_for_task(task) -> str:
    skill_name = _normalize_skill_name(getattr(task, "skill_name", None))
    if skill_name:
        return skill_name
    return _normalize_skill_name(_input_payload_for_task(task).get("skill_name"))


def _reset_task_for_dispatch(task, *, input_payload: dict, title: str | None = None, task_session_id: str | None = None):
    task.input_payload_json = json.dumps(input_payload)
    if title:
        task.title = title
    if task_session_id:
        task.task_session_id = task_session_id
    task.status = "queued"
    task.runtime_request_id = None
    task.summary = None
    task.error_message = None
    task.started_at = None
    task.finished_at = None
    task.result_payload_json = None
    return task


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
    upsert_task_execution_queued_best_effort(db, task=task, agent=assignee_agent, user=user)
    task_dispatcher_service.dispatch_task_in_background(task.id)
    return _task_response(db, task, user)


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
    if not _can_manage_task(db, target_task, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to continue task")
    if (target_task.status or "").strip().lower() in ACTIVE_TASK_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task is still running")

    root_task_id = _root_id_for_task(target_task)
    if _chain_has_active_task(db, root_task_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task chain is still running")
    skill_name = _skill_name_for_task(target_task)
    if not skill_name:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task is missing a selected skill")

    existing_input = _input_payload_for_task(target_task)
    original_task = str(existing_input.get("original_task") or existing_input.get("user_task") or "").strip()
    task_session_id = (target_task.task_session_id or f"agent-task:{root_task_id}").strip()
    task_input = _agent_async_payload(
        task_content=task_content,
        skill_name=skill_name,
        task_session_id=task_session_id,
        root_task_id=root_task_id,
        parent_task_id=target_task.parent_task_id,
        original_task=original_task,
        is_followup=True,
    )
    target_task = _reset_task_for_dispatch(target_task, input_payload=task_input, task_session_id=task_session_id)
    target_task.skill_name = skill_name
    target_task.root_task_id = root_task_id
    target_task = AgentTaskRepository(db).save(target_task)
    assignee_agent = AgentRepository(db).get_by_id(target_task.assignee_agent_id)
    upsert_task_execution_queued_best_effort(db, task=target_task, agent=assignee_agent, user=user)
    task_dispatcher_service.dispatch_task_in_background(target_task.id)
    return _task_response(db, target_task, user)


@router.post("/api/agent-tasks/{task_id}/rerun", response_model=AgentTaskResponse)
def rerun_agent_task(task_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    target_task = _require_visible_task(db, task_id, user)
    if target_task.task_type != AGENT_ASYNC_TASK_TYPE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Rerun is only supported for agent async tasks")
    if not _can_manage_task(db, target_task, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to rerun task")
    if (target_task.status or "").strip().lower() in ACTIVE_TASK_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task is still running")

    root_task_id = _root_id_for_task(target_task)
    if _chain_has_active_task(db, root_task_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task chain is still running")
    skill_name = _skill_name_for_task(target_task)
    if not skill_name:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task is missing a selected skill")

    existing_input = _input_payload_for_task(target_task)
    followup_task = str(existing_input.get("followup_task") or "").strip()
    original_task = str(existing_input.get("original_task") or existing_input.get("user_task") or "").strip()
    task_content = followup_task or str(existing_input.get("user_task") or existing_input.get("original_task") or "").strip()
    if not task_content:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task is missing task content")

    task_session_id = f"agent-task:{target_task.id}:{uuid4().hex[:12]}"
    task_input = _agent_async_payload(
        task_content=task_content,
        skill_name=skill_name,
        task_session_id=task_session_id,
        root_task_id=root_task_id,
        parent_task_id=target_task.parent_task_id,
        original_task=original_task,
        is_followup=bool(followup_task),
    )
    target_task = _reset_task_for_dispatch(
        target_task,
        input_payload=task_input,
        title=_derive_task_title(task_content),
        task_session_id=task_session_id,
    )
    target_task.skill_name = skill_name
    target_task.root_task_id = root_task_id
    target_task = AgentTaskRepository(db).save(target_task)
    assignee_agent = AgentRepository(db).get_by_id(target_task.assignee_agent_id)
    upsert_task_execution_queued_best_effort(db, task=target_task, agent=assignee_agent, user=user)
    task_dispatcher_service.dispatch_task_in_background(target_task.id)
    return _task_response(db, target_task, user)


@router.post("/api/agent-tasks/{task_id}/cancel", response_model=AgentTaskResponse)
async def cancel_agent_task(task_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    task = _require_visible_task(db, task_id, user)
    if not _can_manage_task(db, task, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to cancel task")

    normalized_status = (task.status or "").strip().lower()
    if normalized_status in TERMINAL_TASK_STATUSES:
        return _task_response(db, task, user)
    if normalized_status == "queued":
        task.status = "cancelled"
        task.summary = "Task was cancelled before it started."
        task = AgentTaskRepository(db).save(task)
        mark_task_execution_status_best_effort(
            db,
            task=task,
            status=task.status,
            error_code="task_cancelled_before_start",
            result_summary=task.summary,
        )
        return _task_response(db, task, user)
    if normalized_status != "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task is not cancellable")

    try:
        task = await task_dispatcher_service.cancel_task(task.id, db, user=user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return _task_response(db, task, user)
@router.post("/api/agent-tasks", response_model=AgentTaskResponse)
def create_agent_task(payload: AgentTaskCreateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    assignee_agent = AgentRepository(db).get_by_id(payload.assignee_agent_id)
    if not assignee_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignee agent not found")
    if not _can_write(assignee_agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    create_payload = payload.model_dump()
    create_payload["owner_user_id"] = assignee_agent.owner_user_id
    create_payload["created_by_user_id"] = user.id
    task = AgentTaskRepository(db).create(**create_payload)
    return _task_response(db, task, user)


@router.get("/api/agent-tasks", response_model=list[AgentTaskResponse])
def list_agent_tasks(user=Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admin can list all tasks")
    tasks = AgentTaskRepository(db).list_all()
    return [_task_response(db, task, user) for task in tasks]


@router.get("/api/my/tasks", response_model=list[AgentTaskListItemResponse])
def list_my_tasks(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    owner: str | None = Query(default=None, max_length=32),
    q: str | None = Query(default=None, max_length=120),
):
    tasks = AgentTaskRepository(db).list_visible_to_user_summaries(
        user_id=user.id,
        limit=limit,
        offset=offset,
        status=status_filter,
        owner=owner,
        query=q,
    )
    return [_task_list_item_response(task, user) for task in tasks]


@router.get("/api/agent-tasks/{task_id}", response_model=AgentTaskResponse)
def get_agent_task(task_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    task = AgentTaskRepository(db).get_by_id(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if user.role != "admin":
        if not _is_task_visible_to_user(task, user):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    return _task_response(db, task, user)


@router.get("/api/agents/{agent_id}/tasks", response_model=list[AgentTaskResponse])
def list_agent_tasks_by_agent(agent_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if user.role != "admin" and agent.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    tasks = AgentTaskRepository(db).list_by_agent(agent_id)
    return [_task_response(db, task, user) for task in tasks]


@router.post("/api/agent-tasks/{task_id}/dispatch", status_code=status.HTTP_202_ACCEPTED)
async def dispatch_agent_task(task_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    task = AgentTaskRepository(db).get_by_id(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    assignee_agent = AgentRepository(db).get_by_id(task.assignee_agent_id)
    if not assignee_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignee agent not found")
    if user.role != "admin" and assignee_agent.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to dispatch task")

    if task.status != "queued":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task is not dispatchable")

    upsert_task_execution_queued_best_effort(db, task=task, agent=assignee_agent, user=user)
    logger.info(
        "Manual task dispatch scheduled task_id=%s task_type=%s assignee_agent_id=%s",
        task.id,
        task.task_type,
        task.assignee_agent_id,
    )
    task_dispatcher_service.dispatch_task_in_background(task_id=task_id)
    return {
        "accepted": True,
        "task_id": task.id,
        "task_status": task.status,
        "message": "Task scheduled for background dispatch",
    }
