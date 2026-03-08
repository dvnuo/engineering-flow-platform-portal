from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.repositories.agent_repo import AgentRepository
from app.services.proxy_service import ProxyService

router = APIRouter(tags=["proxy"])
proxy_service = ProxyService()


def _can_access(agent, user) -> bool:
    return user.role == "admin" or agent.owner_user_id == user.id or agent.visibility == "public"


@router.api_route("/a/{agent_id}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@router.api_route("/a/{agent_id}/{subpath:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_agent(
    agent_id: str,
    request: Request,
    subpath: str = "",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not _can_access(agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if agent.status != "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Agent is not running")

    try:
        status_code, content, content_type = await proxy_service.forward(
            agent=agent,
            method=request.method,
            subpath=subpath,
            query_items=request.query_params.multi_items(),
            body=(await request.body()) or None,
            headers=dict(request.headers),
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Proxy upstream failure: {exc}") from exc

    return Response(status_code=status_code, content=content, media_type=content_type)
