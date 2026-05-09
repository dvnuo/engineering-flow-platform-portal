from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.deps import get_current_user
from app.services.copilot_auth_service import copilot_auth_service

router = APIRouter(prefix="/api/copilot/auth", tags=["copilot"])


class StartAuthRequest(BaseModel):
    # Backward compatibility: accepted but ignored by CopilotAuthService because
    # Copilot OAuth must always use https://github.com.
    github_base_url: str | None = None
    runtime_type: str | None = None
    runtime: str | None = None


class AuthCheckRequest(BaseModel):
    auth_id: str
    device_code: str


@router.post("/start")
async def start_auth(request: StartAuthRequest, user=Depends(get_current_user)):
    runtime_type = request.runtime_type or request.runtime
    try:
        status_code, payload = await copilot_auth_service.start_authorization(
            user_id=str(user.id),
            github_base_url=request.github_base_url,
            runtime_type=runtime_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(status_code=status_code, content=payload)


@router.post("/check")
async def check_auth(request: AuthCheckRequest, user=Depends(get_current_user)):
    status_code, payload = await copilot_auth_service.check_authorization(
        user_id=str(user.id),
        auth_id=request.auth_id,
        device_code=request.device_code,
    )
    return JSONResponse(status_code=status_code, content=payload)
