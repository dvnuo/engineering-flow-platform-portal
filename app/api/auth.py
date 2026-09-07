from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
import logging
logger = logging.getLogger(__name__)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.redaction import sanitize_exception_message
from app.repositories.user_repo import UserRepository
from app.repositories.user_allowlist_repo import UserAllowlistRepository
from app.schemas.auth import LoginRequest, MeResponse
from app.services.auth_service import issue_session_token, set_session_cookie, verify_password
from app.services.copilot_auth_service import copilot_auth_service
from app.services.external_login_service import (
    attach_copilot_token_to_default_profile,
    fetch_github_user,
    provision_external_user,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


# Self-service registration is gone: accounts are provisioned on first
# sign-in through SSO or GitHub Copilot (see external_login_service).


@router.post("/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    """Password sign-in, kept for the bootstrap admin (served at /admlogin)."""
    repo = UserRepository(db)
    user = repo.get_by_username_case_insensitive(payload.username)
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not UserAllowlistRepository(db).get_active_by_username(user.username):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is not on the active allowlist",
        )

    repo.mark_login(user)

    set_session_cookie(response, issue_session_token(user.id))
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(settings.session_cookie_name)
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
def me(user=Depends(get_current_user)):
    return MeResponse(
        id=user.id,
        username=user.username,
        nickname=user.nickname,
        role=user.role,
        onboarding_completed=getattr(user, "onboarding_completed_at", None) is not None,
    )


@router.post("/me/onboarding-complete", response_model=MeResponse)
def complete_onboarding(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Mark the first-run tour as seen.

    Recorded per member rather than inferred from "owns zero assistants" so
    someone who deletes their first assistant is not walked through the tour
    again.
    """
    if getattr(user, "onboarding_completed_at", None) is None:
        user.onboarding_completed_at = datetime.utcnow()
        db.add(user)
        db.commit()
        db.refresh(user)
    return MeResponse(
        id=user.id,
        username=user.username,
        nickname=user.nickname,
        role=user.role,
        onboarding_completed=True,
    )


# --- GitHub Copilot sign-in ---------------------------------------------------
#
# Same GitHub device flow the runtime-profile panel uses to connect Copilot,
# run before there is a session. The browser starts a flow, the member
# authorizes on GitHub, and the poll that sees "authorized" turns the token
# into a portal account + session. The token is never returned to the
# browser here; on a first sign-in it goes straight onto the member's default
# runtime profile.


def _copilot_flow_owner(flow_id: str) -> str:
    return f"login:{flow_id}"


class CopilotLoginCheckRequest(BaseModel):
    flow_id: str
    auth_id: str
    device_code: str


def _require_copilot_login_enabled() -> None:
    if not settings.copilot_login_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="GitHub Copilot sign-in is disabled")


@router.post("/copilot/start")
async def copilot_login_start():
    _require_copilot_login_enabled()
    flow_id = uuid4().hex
    status_code, payload = await copilot_auth_service.start_authorization(
        user_id=_copilot_flow_owner(flow_id),
        github_base_url=None,
    )
    if status_code == 200:
        payload = {**payload, "flow_id": flow_id}
    return JSONResponse(status_code=status_code, content=payload)


@router.post("/copilot/check")
async def copilot_login_check(payload: CopilotLoginCheckRequest, db: Session = Depends(get_db)):
    _require_copilot_login_enabled()
    flow_id = (payload.flow_id or "").strip()
    if not flow_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="flow_id required")
    status_code, result = await copilot_auth_service.check_authorization(
        user_id=_copilot_flow_owner(flow_id),
        auth_id=payload.auth_id,
        device_code=payload.device_code,
    )
    if status_code != 200 or result.get("status") != "authorized":
        return JSONResponse(status_code=status_code, content=result)

    token = str(result.get("token") or (result.get("oauth") or {}).get("access") or "").strip()
    if not token:
        return JSONResponse(status_code=200, content={"status": "failed", "message": "GitHub returned no token"})

    try:
        github_user = await fetch_github_user(token)
    except Exception as exc:
        logger.exception("GitHub user lookup failed after Copilot authorization")
        return JSONResponse(
            status_code=200,
            content={"status": "failed", "message": f"GitHub user lookup failed: {sanitize_exception_message(exc)}"},
        )

    try:
        user, created = provision_external_user(
            db,
            username=github_user["login"],
            display_name=github_user.get("name") or "",
            source="github_copilot",
        )
        if created:
            attach_copilot_token_to_default_profile(db, user, token)
    except Exception as exc:
        db.rollback()
        logger.exception("Provisioning portal user after Copilot authorization failed")
        return JSONResponse(
            status_code=200,
            content={"status": "failed", "message": f"Sign-in failed: {sanitize_exception_message(exc)}"},
        )

    response = JSONResponse(
        status_code=200,
        content={"status": "authorized", "redirect": "/app", "username": user.username, "created": created},
    )
    set_session_cookie(response, issue_session_token(user.id))
    return response
