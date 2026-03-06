from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.repositories.robot_repo import RobotRepository
from app.services.proxy_service import ProxyService

router = APIRouter(tags=["proxy"])
proxy_service = ProxyService()


def _can_access(robot, user) -> bool:
    return user.role == "admin" or robot.owner_user_id == user.id or robot.visibility == "public"


@router.api_route("/r/{robot_id}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@router.api_route("/r/{robot_id}/{subpath:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_robot(
    robot_id: str,
    request: Request,
    subpath: str = "",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    robot = RobotRepository(db).get_by_id(robot_id)
    if not robot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Robot not found")
    if not _can_access(robot, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    if robot.status != "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Robot is not running")

    body = await request.body()
    query = {k: v for k, v in request.query_params.multi_items()}
    status_code, content, content_type = await proxy_service.forward(
        robot=robot,
        method=request.method,
        subpath=subpath,
        query_params=query,
        body=body or None,
        headers=dict(request.headers),
    )
    return Response(status_code=status_code, content=content, media_type=content_type)
