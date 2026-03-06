from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.repositories.robot_repo import RobotRepository
from app.schemas.robot import RobotCreateRequest, RobotResponse

router = APIRouter(prefix="/api/robots", tags=["robots"])


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
    base = payload.name.lower().replace(" ", "-")
    robot = RobotRepository(db).create(
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
        namespace="robots",
        deployment_name=f"robot-{base}",
        service_name=f"robot-{base}-svc",
        pvc_name=f"robot-{base}-pvc",
    )
    return RobotResponse.model_validate(robot)
