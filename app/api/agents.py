from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.repositories.audit_repo import AuditRepository
from app.repositories.agent_repo import AgentRepository
from app.schemas.agent import (
    AgentCreateRequest,
    AgentDeleteResponse,
    AgentResponse,
    AgentStatusResponse,
    AgentUpdateRequest,
)
from app.services.k8s_service import K8sService
from app.services.proxy_service import ProxyService
from app.utils.naming import runtime_names
from app.utils.state_machine import can_transition, is_valid_status

router = APIRouter(prefix="/api/agents", tags=["agents"])
settings = get_settings()


@router.get("/defaults")
def get_agent_defaults(user=Depends(get_current_user)):
    """Get default configuration for agent creation."""
    return {
        "image_repo": settings.default_agent_image_repo,
        "image_tag": settings.default_agent_image_tag,
        "git_image": settings.default_agent_git_image,
        "default_repo_url": settings.default_agent_repo_url,
        "default_branch": settings.default_agent_branch,
        "disk_size_gi": settings.default_agent_disk_size_gi,
        "cpu": settings.default_agent_cpu,
        "memory": settings.default_agent_memory,
        "mount_path": settings.default_agent_mount_path,
    }


k8s_service = K8sService()
proxy_service = ProxyService()


def _can_read(agent, user) -> bool:
    return user.role == "admin" or agent.owner_user_id == user.id or agent.visibility == "public"


def _can_write(agent, user) -> bool:
    return user.role == "admin" or agent.owner_user_id == user.id


def _load_writable_agent(agent_id: str, user, db: Session):
    repo = AgentRepository(db)
    agent = repo.get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not _can_write(agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return repo, agent


def _delete_agent_with_mode(repo: AgentRepository, agent, user, db: Session, destroy_data: bool):
    agent.status = "deleting"
    repo.save(agent)

    runtime = k8s_service.delete_agent_runtime(agent, destroy_data=destroy_data)
    if runtime.status == "failed":
        agent.status = "failed"
        agent.last_error = runtime.message
        repo.save(agent)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=runtime.message or "Delete failed")

    repo.delete(agent)
    AuditRepository(db).create(
        action="delete_agent",
        target_type="agent",
        target_id=agent.id,
        user_id=user.id,
        details={"destroy_data": destroy_data},
    )
    return {"ok": True, "destroy_data": destroy_data}


@router.get("/mine", response_model=list[AgentResponse])
def list_mine(user=Depends(get_current_user), db: Session = Depends(get_db)):
    agents = AgentRepository(db).list_by_owner(user.id)
    return [AgentResponse.model_validate(r) for r in agents]


@router.get("/public", response_model=list[AgentResponse])
def list_public(user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    agents = AgentRepository(db).list_public()
    return [AgentResponse.model_validate(r) for r in agents]


@router.post("", response_model=AgentResponse)
def create_agent(payload: AgentCreateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo = AgentRepository(db)
    agent = repo.create(
        name=payload.name,
        description=payload.description,
        owner_user_id=user.id,
        visibility="private",
        status="creating",
        image=payload.image,
        repo_url=payload.repo_url,
        branch=payload.branch,
        cpu=payload.cpu,
        memory=payload.memory,
        disk_size_gi=payload.disk_size_gi,
        mount_path=payload.mount_path,
        namespace=settings.agents_namespace,
        deployment_name="",
        service_name="",
        pvc_name="",
        endpoint_path="",
    )

    agent.deployment_name, agent.service_name, agent.pvc_name, agent.endpoint_path = runtime_names(agent.id)
    repo.save(agent)

    runtime = k8s_service.create_agent_runtime(agent)
    agent.status = runtime.status
    agent.last_error = runtime.message
    repo.save(agent)

    AuditRepository(db).create(
        action="create_agent",
        target_type="agent",
        target_id=agent.id,
        user_id=user.id,
        details={"name": agent.name, "image": agent.image, "status": agent.status},
    )
    return AgentResponse.model_validate(agent)


@router.patch("/{agent_id}", response_model=AgentResponse)
def update_agent(agent_id: str, payload: AgentUpdateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo, agent = _load_writable_agent(agent_id, user, db)

    changes = payload.model_dump(exclude_unset=True)
    if "disk_size_gi" in changes and changes["disk_size_gi"] is not None and changes["disk_size_gi"] < 1:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="disk_size_gi must be >= 1")

    for field, value in changes.items():
        setattr(agent, field, value)

    repo.save(agent)
    
    # Update K8s runtime if repo_url or branch changed
    if "repo_url" in changes or "branch" in changes:
        k8s_service.update_agent_runtime(agent)

    AuditRepository(db).create(
        action="update_agent",
        target_type="agent",
        target_id=agent.id,
        user_id=user.id,
        details=changes,
    )
    return AgentResponse.model_validate(agent)


@router.get("/{agent_id}", response_model=AgentResponse)
def get_agent(agent_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not _can_read(agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return AgentResponse.model_validate(agent)


@router.get("/{agent_id}/git-info")
def get_agent_git_info(agent_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Get git commit info from running agent."""
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not _can_read(agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    
    if agent.status != "running":
        return {"commit_id": None, "repo_url": None, "status": agent.status}
    
    # Try to get git info from agent
    try:
        print(f"DEBUG: Calling git-info for agent {agent_id}, service={agent.service_name}")
        status_code, content, _ = proxy_service.forward(
            agent=agent,
            method="GET",
            subpath="api/git-info",
            query_items=[],
            body=None,
            headers={},
        )
        print(f"DEBUG: status={status_code}, content={content}")
        if status_code == 200:
            import json
            return json.loads(content.decode("utf-8"))
    except Exception as e:
        print(f"DEBUG: Error getting git-info: {e}")
        pass
    
    return {"commit_id": None, "repo_url": None, "status": "error"}


@router.post("/{agent_id}/start", response_model=AgentResponse)
def start_agent(agent_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo, agent = _load_writable_agent(agent_id, user, db)

    if not can_transition(agent.status, "running"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Cannot start agent from status '{agent.status}'")

    runtime = k8s_service.start_agent(agent)
    agent.status = runtime.status
    agent.last_error = runtime.message
    repo.save(agent)
    AuditRepository(db).create("start_agent", "agent", agent.id, user.id)
    return AgentResponse.model_validate(agent)


@router.post("/{agent_id}/stop", response_model=AgentResponse)
def stop_agent(agent_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo, agent = _load_writable_agent(agent_id, user, db)

    if not can_transition(agent.status, "stopped"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Cannot stop agent from status '{agent.status}'")

    runtime = k8s_service.stop_agent(agent)
    agent.status = runtime.status
    agent.last_error = runtime.message
    repo.save(agent)
    AuditRepository(db).create("stop_agent", "agent", agent.id, user.id)
    return AgentResponse.model_validate(agent)


@router.post("/{agent_id}/restart", response_model=AgentResponse)
def restart_agent(agent_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo, agent = _load_writable_agent(agent_id, user, db)

    # Restart = stop then start
    if agent.status == "running":
        runtime = k8s_service.stop_agent(agent)
        agent.status = runtime.status
        agent.last_error = runtime.message
        repo.save(agent)
    
    runtime = k8s_service.start_agent(agent)
    agent.status = runtime.status
    agent.last_error = runtime.message
    repo.save(agent)
    AuditRepository(db).create("restart_agent", "agent", agent.id, user.id)
    return AgentResponse.model_validate(agent)


@router.post("/{agent_id}/share", response_model=AgentResponse)
def share_agent(agent_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo, agent = _load_writable_agent(agent_id, user, db)
    agent.visibility = "public"
    repo.save(agent)
    AuditRepository(db).create("share_agent", "agent", agent.id, user.id)
    return AgentResponse.model_validate(agent)


@router.post("/{agent_id}/unshare", response_model=AgentResponse)
def unshare_agent(agent_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo, agent = _load_writable_agent(agent_id, user, db)
    agent.visibility = "private"
    repo.save(agent)
    AuditRepository(db).create("unshare_agent", "agent", agent.id, user.id)
    return AgentResponse.model_validate(agent)


@router.delete("/{agent_id}", response_model=AgentDeleteResponse)
def delete_agent(
    agent_id: str,
    destroy_data: bool = Query(False, description="If true, also destroy PVC/data"),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repo, agent = _load_writable_agent(agent_id, user, db)
    return _delete_agent_with_mode(repo, agent, user, db, destroy_data=destroy_data)


@router.post("/{agent_id}/delete-runtime", response_model=AgentDeleteResponse)
def delete_agent_runtime(agent_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo, agent = _load_writable_agent(agent_id, user, db)
    return _delete_agent_with_mode(repo, agent, user, db, destroy_data=False)


@router.post("/{agent_id}/destroy", response_model=AgentDeleteResponse)
def destroy_agent(agent_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo, agent = _load_writable_agent(agent_id, user, db)
    return _delete_agent_with_mode(repo, agent, user, db, destroy_data=True)


@router.get("/{agent_id}/status", response_model=AgentStatusResponse)
def agent_status(agent_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo = AgentRepository(db)
    agent = repo.get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not _can_read(agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    runtime = k8s_service.get_agent_runtime_status(agent)
    agent.status = runtime.status if is_valid_status(runtime.status) else "failed"
    agent.last_error = runtime.message
    repo.save(agent)
    return AgentStatusResponse(
        id=agent.id,
        status=agent.status,
        cpu_usage=runtime.cpu_usage,
        memory_usage=runtime.memory_usage,
        last_error=agent.last_error,
    )
