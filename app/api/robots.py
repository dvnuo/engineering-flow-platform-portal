from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.repositories.audit_repo import AuditRepository
from app.repositories.robot_repo import RobotRepository
from app.schemas.robot import RobotCreateRequest, RobotResponse, RobotStatusResponse
from app.services.k8s_service import K8sService
from app.utils.naming import runtime_names

router = APIRouter(prefix="/api/robots", tags=["robots"])
settings = get_settings()
k8s_service = K8sService()

VALID_STATUSES = {"creating", "running", "stopped", "deleting", "failed"}


def _can_read(robot, user) -> bool:
    return user.role == "admin" or robot.owner_user_id == user.id or robot.visibility == "public"


def _can_write(robot, user) -> bool:
    return user.role == "admin" or robot.owner_user_id == user.id


def _load_writable_robot(robot_id: str, user, db: Session):
    repo = RobotRepository(db)
    robot = repo.get_by_id(robot_id)
    if not robot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Robot not found")
    if not _can_write(robot, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return repo, robot


def _delete_robot_with_mode(repo: RobotRepository, robot, user, db: Session, destroy_data: bool):
    robot.status = "deleting"
    repo.save(robot)

    runtime = k8s_service.delete_robot_runtime(robot, destroy_data=destroy_data)
    if runtime.status == "failed":
        robot.status = "failed"
        robot.last_error = runtime.message
        repo.save(robot)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=runtime.message or "Delete failed")

    repo.delete(robot)
    AuditRepository(db).create(
        action="delete_robot",
        target_type="robot",
        target_id=robot.id,
        user_id=user.id,
        details={"destroy_data": destroy_data},
    )
    return {"ok": True, "destroy_data": destroy_data}


@router.get("/mine", response_model=list[RobotResponse])
def list_mine(user=Depends(get_current_user), db: Session = Depends(get_db)):
    robots = RobotRepository(db).list_by_owner(user.id)
    return [RobotResponse.model_validate(r) for r in robots]


@router.get("/public", response_model=list[RobotResponse])
def list_public(user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    robots = RobotRepository(db).list_public()
    return [RobotResponse.model_validate(r) for r in robots]


@router.post("", response_model=RobotResponse)
def create_robot(payload: RobotCreateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo = RobotRepository(db)
    robot = repo.create(
        name=payload.name,
        description=payload.description,
        owner_user_id=user.id,
        visibility="private",
        status="creating",
        image=payload.image,
        cpu=payload.cpu,
        memory=payload.memory,
        disk_size_gi=payload.disk_size_gi,
        mount_path="/data",
        namespace=settings.robots_namespace,
        deployment_name="",
        service_name="",
        pvc_name="",
        endpoint_path="",
    )

    robot.deployment_name, robot.service_name, robot.pvc_name, robot.endpoint_path = runtime_names(robot.id)
    repo.save(robot)

    runtime = k8s_service.create_robot_runtime(robot)
    robot.status = runtime.status
    robot.last_error = runtime.message
    repo.save(robot)

    AuditRepository(db).create(
        action="create_robot",
        target_type="robot",
        target_id=robot.id,
        user_id=user.id,
        details={"name": robot.name, "image": robot.image, "status": robot.status},
    )
    return RobotResponse.model_validate(robot)


@router.get("/{robot_id}", response_model=RobotResponse)
def get_robot(robot_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    robot = RobotRepository(db).get_by_id(robot_id)
    if not robot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Robot not found")
    if not _can_read(robot, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return RobotResponse.model_validate(robot)


@router.post("/{robot_id}/start", response_model=RobotResponse)
def start_robot(robot_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo, robot = _load_writable_robot(robot_id, user, db)

    runtime = k8s_service.start_robot(robot)
    robot.status = runtime.status
    robot.last_error = runtime.message
    repo.save(robot)
    AuditRepository(db).create("start_robot", "robot", robot.id, user.id)
    return RobotResponse.model_validate(robot)


@router.post("/{robot_id}/stop", response_model=RobotResponse)
def stop_robot(robot_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo, robot = _load_writable_robot(robot_id, user, db)

    runtime = k8s_service.stop_robot(robot)
    robot.status = runtime.status
    robot.last_error = runtime.message
    repo.save(robot)
    AuditRepository(db).create("stop_robot", "robot", robot.id, user.id)
    return RobotResponse.model_validate(robot)


@router.post("/{robot_id}/share", response_model=RobotResponse)
def share_robot(robot_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo, robot = _load_writable_robot(robot_id, user, db)
    robot.visibility = "public"
    repo.save(robot)
    AuditRepository(db).create("share_robot", "robot", robot.id, user.id)
    return RobotResponse.model_validate(robot)


@router.post("/{robot_id}/unshare", response_model=RobotResponse)
def unshare_robot(robot_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo, robot = _load_writable_robot(robot_id, user, db)
    robot.visibility = "private"
    repo.save(robot)
    AuditRepository(db).create("unshare_robot", "robot", robot.id, user.id)
    return RobotResponse.model_validate(robot)


@router.delete("/{robot_id}")
def delete_robot(
    robot_id: str,
    destroy_data: bool = Query(False, description="If true, also destroy PVC/data"),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repo, robot = _load_writable_robot(robot_id, user, db)
    return _delete_robot_with_mode(repo, robot, user, db, destroy_data=destroy_data)


@router.post("/{robot_id}/delete-runtime")
def delete_robot_runtime(robot_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo, robot = _load_writable_robot(robot_id, user, db)
    return _delete_robot_with_mode(repo, robot, user, db, destroy_data=False)


@router.post("/{robot_id}/destroy")
def destroy_robot(robot_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo, robot = _load_writable_robot(robot_id, user, db)
    return _delete_robot_with_mode(repo, robot, user, db, destroy_data=True)


@router.get("/{robot_id}/status", response_model=RobotStatusResponse)
def robot_status(robot_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    repo = RobotRepository(db)
    robot = repo.get_by_id(robot_id)
    if not robot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Robot not found")
    if not _can_read(robot, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    runtime = k8s_service.get_robot_runtime_status(robot)
    robot.status = runtime.status if runtime.status in VALID_STATUSES else "failed"
    robot.last_error = runtime.message
    repo.save(robot)
    return RobotStatusResponse(id=robot.id, status=robot.status, last_error=robot.last_error)
