"""Proxy copilot auth to agent (EFP)."""

import httpx
import logging
logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel

from app.config import get_settings
from app.deps import get_current_user

router = APIRouter(prefix="/api/copilot/auth", tags=["copilot"])
settings = get_settings()


class AuthCheckRequest(BaseModel):
    auth_id: str
    device_code: str


def get_agent_url(agent_id: str) -> str:
    """Get agent base URL from K8s service."""
    # This will be handled by the proxy service
    # For now, construct the URL that will be proxied
    return f"/a/{agent_id}/api/copilot/auth"


@router.post("/start")
async def start_auth(request: Request, user=Depends(get_current_user)):
    """Proxy: forward to agent."""
    # Read body to get agent_id
    try:
        body = await request.body()
    except Exception:
        body = None
    
    # Return a redirect response - the UI should call the agent proxy directly
    # This endpoint exists for compatibility but the real work happens in the UI
    raise HTTPException(status_code=400, detail="Use agent proxy: /a/{agent_id}/api/copilot/auth/start")


@router.post("/check")
async def check_auth(request: AuthCheckRequest, user=Depends(get_current_user)):
    """Proxy: forward to agent."""
    raise HTTPException(status_code=400, detail="Use agent proxy: /a/{agent_id}/api/copilot/auth/check")
