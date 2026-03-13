"""Copilot auth endpoints - deprecated, use agent proxy instead."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from app.deps import get_current_user

router = APIRouter(prefix="/api/copilot/auth", tags=["copilot"])


class AuthCheckRequest(BaseModel):
    auth_id: str
    device_code: str


@router.post("/start")
async def start_auth(user=Depends(get_current_user)):
    """Deprecated: Use /a/{agent_id}/api/copilot/auth/start instead."""
    raise HTTPException(
        status_code=410, 
        detail="Deprecated: Call /a/{agent_id}/api/copilot/auth/start directly"
    )


@router.post("/check")
async def check_auth(request: AuthCheckRequest, user=Depends(get_current_user)):
    """Deprecated: Use /a/{agent_id}/api/copilot/auth/check instead."""
    raise HTTPException(
        status_code=410, 
        detail="Deprecated: Call /a/{agent_id}/api/copilot/auth/check directly"
    )
