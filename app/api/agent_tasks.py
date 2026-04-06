from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.schemas.agent_task import AgentTaskCreateRequest, AgentTaskResponse

router = APIRouter(tags=["agent-tasks"])


@router.post("/api/agent-tasks", response_model=AgentTaskResponse)
def create_agent_task(payload: AgentTaskCreateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user

    assignee_agent = AgentRepository(db).get_by_id(payload.assignee_agent_id)
    if not assignee_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignee agent not found")

    if payload.parent_agent_id is not None:
        parent_agent = AgentRepository(db).get_by_id(payload.parent_agent_id)
        if not parent_agent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent agent not found")

    task = AgentTaskRepository(db).create(**payload.model_dump())
    return AgentTaskResponse.model_validate(task)


@router.get("/api/agent-tasks", response_model=list[AgentTaskResponse])
def list_agent_tasks(user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    tasks = AgentTaskRepository(db).list_all()
    return [AgentTaskResponse.model_validate(task) for task in tasks]


@router.get("/api/agents/{agent_id}/tasks", response_model=list[AgentTaskResponse])
def list_agent_tasks_by_agent(agent_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    tasks = AgentTaskRepository(db).list_by_agent(agent_id)
    return [AgentTaskResponse.model_validate(task) for task in tasks]
