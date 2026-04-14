from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, require_admin
from app.repositories.audit_repo import AuditRepository
from app.repositories.user_repo import UserRepository
from app.schemas.user import PasswordUpdateRequest, UserCreateRequest, UserResponse
from app.services.auth_service import hash_password
from app.services.runtime_profile_service import RuntimeProfileService

router = APIRouter(prefix="/api/users", tags=["users"])


@router.post("", response_model=UserResponse)
def create_user(payload: UserCreateRequest, admin=Depends(require_admin), db: Session = Depends(get_db)):
    repo = UserRepository(db)
    existing = repo.get_by_username(payload.username)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")

    user = repo.create(payload.username, hash_password(payload.password), payload.role)
    RuntimeProfileService(db).ensure_user_has_default_profile(user)
    AuditRepository(db).create(
        action="create_user",
        target_type="user",
        target_id=str(user.id),
        user_id=admin.id,
        details={"username": user.username, "role": user.role},
    )
    return UserResponse.model_validate(user)


@router.get("", response_model=list[UserResponse])
def list_users(_: object = Depends(require_admin), db: Session = Depends(get_db)):
    users = UserRepository(db).list_all()
    return [UserResponse.model_validate(user) for user in users]


@router.patch("/{user_id}/password")
def update_password(
    user_id: int,
    payload: PasswordUpdateRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repo = UserRepository(db)
    user = repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if current_user.role != "admin" and current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    repo.update_password(user, hash_password(payload.password))
    AuditRepository(db).create(
        action="change_password",
        target_type="user",
        target_id=str(user.id),
        user_id=current_user.id,
    )
    return {"ok": True}
