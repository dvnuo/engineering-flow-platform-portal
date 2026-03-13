"""Proxy copilot auth to EFP."""

import httpx
import logging
logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import uuid
from datetime import datetime

from app.config import get_settings

router = APIRouter(prefix="/api/copilot/auth", tags=["copilot"])
settings = get_settings()


class AuthStartResponse(BaseModel):
    auth_id: str
    device_code: str
    user_code: str
    verification_url: str
    verification_complete_url: str
    expires_in: int
    interval: int


class AuthCheckRequest(BaseModel):
    auth_id: str
    device_code: str


class AuthCheckResponse(BaseModel):
    status: str
    message: str = ""
    token: str = ""


def get_efp_url() -> str:
    """Get EFP endpoint URL."""
    return settings.efp_endpoint.rstrip("/")


@router.post("/start")
async def start_auth(request: Request):
    """Forward GitHub Copilot auth start to EFP."""
    try:
        efp_url = get_efp_url()
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Read body if present
            body = await request.body()
            
            resp = await client.post(
                f"{efp_url}/api/copilot/auth/start",
                content=body,
                headers={"Content-Type": "application/json"},
            )
            
            if resp.status_code != 200:
                logger.error(f"EFP copilot auth start failed: {resp.status_code}")
                return {"error": f"EFP returned {resp.status_code}"}
            
            return resp.json()
            
    except Exception as e:
        logger.exception("Failed to forward to EFP")
        raise HTTPException(status_code=502, detail=f"Failed to connect to EFP: {e}")


@router.post("/check")
async def check_auth(request: AuthCheckRequest):
    """Forward GitHub Copilot auth check to EFP."""
    try:
        efp_url = get_efp_url()
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{efp_url}/api/copilot/auth/check",
                json=request.model_dump(),
            )
            
            if resp.status_code != 200:
                logger.error(f"EFP copilot auth check failed: {resp.status_code}")
                return {"status": "failed", "message": f"EFP returned {resp.status_code}"}
            
            return resp.json()
            
    except Exception as e:
        logger.exception("Failed to forward to EFP")
        raise HTTPException(status_code=502, detail=f"Failed to connect to EFP: {e}")
