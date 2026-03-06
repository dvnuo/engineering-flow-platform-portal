from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
from app.repositories.user_repo import UserRepository
from app.schemas.user import UserCreateRequest, UserResponse
from app.services.auth_service import hash_password

router = APIRouter(prefix="/api/users", tags=["users"])


@router.post("", response_model=UserResponse)
def create_user(payload: UserCreateRequest, _: object = Depends(require_admin), db: Session = Depends(get_db)):
    repo = UserRepository(db)
    existing = repo.get_by_username(payload.username)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")

    user = repo.create(payload.username, hash_password(payload.password), payload.role)
    return UserResponse.model_validate(user)


@router.get("", response_model=list[UserResponse])
def list_users(_: object = Depends(require_admin), db: Session = Depends(get_db)):
    users = UserRepository(db).list_all()
    return [UserResponse.model_validate(user) for user in users]
