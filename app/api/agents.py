from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.repositories.audit_repo import AuditRepository
from app.repositories.agent_repo import AgentRepository
from app.repositories.capability_profile_repo import CapabilityProfileRepository
from app.repositories.policy_profile_repo import PolicyProfileRepository
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.schemas.agent import (
    ALLOWED_AGENT_TYPES,
    AgentChatModelProfileResponse,
    AgentCreateRequest,
    AgentDeleteResponse,
    AgentResponse,
    AgentStatusResponse,
    AgentUpdateRequest,
)
from app.schemas.runtime_profile import parse_runtime_profile_config_json
from app.services.k8s_service import K8sService
from app.services.runtime_profile_service import RuntimeProfileService
from app.services.runtime_profile_sync_service import RuntimeProfileSyncService
from app.utils.git_urls import normalize_git_repo_url
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
        "default_runtime_repo_url": _runtime_repo_url_from_settings(),
        "default_runtime_branch": _runtime_branch_from_settings(),
        "default_skill_repo_url": normalize_git_repo_url(settings.default_skill_repo_url),
        "default_skill_branch": settings.default_skill_branch,
        "default_repo_url": normalize_git_repo_url(settings.default_skill_repo_url),
        "default_branch": settings.default_skill_branch,
        "disk_size_gi": settings.default_agent_disk_size_gi,
        "cpu": settings.default_agent_cpu,
        "memory": settings.default_agent_memory,
        "mount_path": settings.default_agent_mount_path,
    }


k8s_service = K8sService()
runtime_profile_sync_service = RuntimeProfileSyncService()


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


def _validate_profile_references(
    db: Session,
    capability_profile_id: str | None,
    policy_profile_id: str | None,
    runtime_profile_id: str | None,
    current_user_id: int | None = None,
) -> None:
    if capability_profile_id is not None:
        capability_profile = CapabilityProfileRepository(db).get_by_id(capability_profile_id)
        if not capability_profile:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CapabilityProfile not found")

    if policy_profile_id is not None:
        policy_profile = PolicyProfileRepository(db).get_by_id(policy_profile_id)
        if not policy_profile:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PolicyProfile not found")

    if runtime_profile_id is not None:
        runtime_profile = RuntimeProfileRepository(db).get_by_id(runtime_profile_id)
        if not runtime_profile or (current_user_id is not None and runtime_profile.owner_user_id != current_user_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RuntimeProfile not found")


def _validate_agent_type_or_422(agent_type: str | None) -> None:
    if agent_type is None:
        return
    if agent_type not in ALLOWED_AGENT_TYPES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="agent_type must be one of: workspace, specialist, task")


def _default_agent_image() -> str:
    return f"{settings.default_agent_image_repo}:{settings.default_agent_image_tag}"


def _runtime_repo_url_from_settings() -> str | None:
    return normalize_git_repo_url(settings.default_agent_runtime_repo_url or settings.default_agent_repo_url)


def _runtime_branch_from_settings() -> str:
    return (settings.default_agent_runtime_branch or settings.default_agent_branch or "master").strip() or "master"


def _resolve_create_skill_repo_url(payload: AgentCreateRequest) -> str | None:
    if "skill_repo_url" in payload.model_fields_set:
        return payload.skill_repo_url
    return normalize_git_repo_url(settings.default_skill_repo_url)


def _resolve_create_skill_branch(payload: AgentCreateRequest) -> str:
    branch = (payload.skill_branch or "").strip()
    return branch or settings.default_skill_branch or "master"


def _resolve_create_image(payload: AgentCreateRequest) -> str:
    image = (payload.image or "").strip()
    return image or _default_agent_image()


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
    _validate_profile_references(db, payload.capability_profile_id, payload.policy_profile_id, payload.runtime_profile_id, current_user_id=user.id)
    _validate_agent_type_or_422(payload.agent_type)
    runtime_profile_id = payload.runtime_profile_id
    if runtime_profile_id is None:
        runtime_profile_id = RuntimeProfileService(db).ensure_user_has_default_profile(user).id

    effective_image = _resolve_create_image(payload)
    effective_runtime_repo_url = _runtime_repo_url_from_settings()
    effective_runtime_branch = _runtime_branch_from_settings()
    effective_skill_repo_url = _resolve_create_skill_repo_url(payload)
    effective_skill_branch = _resolve_create_skill_branch(payload)

    repo = AgentRepository(db)
    agent = repo.create(
        name=payload.name,
        description=payload.description,
        owner_user_id=user.id,
        visibility="private",
        status="creating",
        image=effective_image,
        repo_url=effective_runtime_repo_url,
        branch=effective_runtime_branch,
        skill_repo_url=effective_skill_repo_url,
        skill_branch=effective_skill_branch,
        cpu=payload.cpu,
        memory=payload.memory,
        agent_type=payload.agent_type,
        capability_profile_id=payload.capability_profile_id,
        policy_profile_id=payload.policy_profile_id,
        runtime_profile_id=runtime_profile_id,
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
        details={"name": agent.name, "image": effective_image, "status": agent.status, "skill_repo_url": effective_skill_repo_url, "skill_branch": effective_skill_branch},
    )
    return AgentResponse.model_validate(agent)


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(agent_id: str, payload: AgentUpdateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo, agent = _load_writable_agent(agent_id, user, db)

    changes = payload.model_dump(exclude_unset=True)
    changes.pop("repo_url", None)
    changes.pop("branch", None)

    if "capability_profile_id" in changes and changes["capability_profile_id"] is not None:
        _validate_profile_references(db, changes["capability_profile_id"], None, None)
    if "policy_profile_id" in changes and changes["policy_profile_id"] is not None:
        _validate_profile_references(db, None, changes["policy_profile_id"], None)

    if "runtime_profile_id" in changes:
        if changes["runtime_profile_id"] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="runtime_profile_id cannot be null; choose one of the user's runtime profiles.",
            )
        _validate_profile_references(db, None, None, changes["runtime_profile_id"], current_user_id=user.id)

    if "disk_size_gi" in changes and changes["disk_size_gi"] is not None and changes["disk_size_gi"] < 1:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="disk_size_gi must be >= 1")
    if "agent_type" in changes:
        if changes["agent_type"] is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="agent_type cannot be null")
        _validate_agent_type_or_422(changes["agent_type"])

    for field, value in changes.items():
        setattr(agent, field, value)

    repo.save(agent)

    if "runtime_profile_id" in changes and (agent.status or "").lower() == "running":
        runtime_profile_id = changes.get("runtime_profile_id")
        payload_data = runtime_profile_sync_service.build_clear_payload()
        if runtime_profile_id:
            profile = RuntimeProfileRepository(db).get_by_id(runtime_profile_id)
            if profile:
                payload_data = runtime_profile_sync_service.build_apply_payload_from_profile(profile)
        await runtime_profile_sync_service.push_payload_to_agent(agent, payload_data)

    # Update K8s runtime if skill repo settings changed
    if "skill_repo_url" in changes or "skill_branch" in changes:
        runtime = k8s_service.update_agent_runtime(agent)
        if runtime.status == "failed":
            agent.last_error = runtime.message
            repo.save(agent)

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


@router.get("/{agent_id}/chat-model-profile", response_model=AgentChatModelProfileResponse)
def get_agent_chat_model_profile(agent_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not _can_read(agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    runtime_profile_id = str(agent.runtime_profile_id or "").strip()
    if not runtime_profile_id:
        return AgentChatModelProfileResponse()

    profile = RuntimeProfileRepository(db).get_by_id(runtime_profile_id)
    if not profile:
        return AgentChatModelProfileResponse()

    parsed = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
    llm = parsed.get("llm") if isinstance(parsed, dict) else {}
    if not isinstance(llm, dict):
        llm = {}
    provider = RuntimeProfileService.normalize_managed_llm_provider(str(llm.get("provider") or ""))
    current_model = str(llm.get("model") or "").strip()

    return {
        "runtime_profile_id": profile.id,
        "revision": profile.revision,
        "provider": provider,
        "current_model": current_model,
    }


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

    # Restart = stop then start, with same state-machine checks as /stop and /start
    if agent.status == "running":
        if not can_transition(agent.status, "stopped"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot stop agent from status '{agent.status}'",
            )
        runtime = k8s_service.stop_agent(agent)
        agent.status = runtime.status
        agent.last_error = runtime.message
        repo.save(agent)

    # Always enforce state-machine guard before starting
    if not can_transition(agent.status, "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot start agent from status '{agent.status}'",
        )
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
