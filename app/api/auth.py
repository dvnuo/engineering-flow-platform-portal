from fastapi import APIRouter, Depends, HTTPException, Response, status
import logging
logger = logging.getLogger(__name__)
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.repositories.audit_repo import AuditRepository
from app.repositories.user_repo import UserRepository
from app.schemas.auth import LoginRequest, MeResponse, RegisterRequest
from app.services.auth_service import hash_password, issue_session_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


@router.post("/register")
def register(payload: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    repo = UserRepository(db)
    
    # Check if username exists
    existing = repo.get_by_username(payload.username)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")
    
    # Create new user (default role: user)
    # Use transaction to prevent race condition
    try:
        user = repo.create(payload.username, hash_password(payload.password), "user", payload.nickname)
    except Exception as e:
        logger.exception("Auth error")
        db.rollback()
        # Check if it's a duplicate key error
        if "duplicate key" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")
        raise
    
    # Audit trail for registration
    AuditRepository(db).create(
        action="register",
        target_type="user",
        target_id=str(user.id),
        user_id=user.id,
        details={"username": user.username},
    )
    
    # Auto-login
    token = issue_session_token(user.id)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=365 * 24 * 60 * 60,  # 1 year
    )
    return {"ok": True, "username": user.username, "role": user.role}


@router.post("/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    repo = UserRepository(db)
    user = repo.get_by_username(payload.username)
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = issue_session_token(user.id)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=365 * 24 * 60 * 60,  # 1 year
    )
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(settings.session_cookie_name)
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
def me(user=Depends(get_current_user)):
    return MeResponse(id=user.id, username=user.username, nickname=user.nickname, role=user.role)
