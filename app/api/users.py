from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import require_admin
from app.repositories.audit_repo import AuditRepository
from app.repositories.user_allowlist_repo import UserAllowlistRepository, normalize_username
from app.repositories.user_repo import UserRepository
from app.schemas.user import (
    AllowlistCreateRequest,
    AllowlistResponse,
    MemberOverviewResponse,
    UserAdminUpdateRequest,
    UserCreateRequest,
    UserResponse,
)
from app.services.access_control_service import AccessControlService
from app.services.auth_service import hash_password
from app.services.member_management_service import MemberManagementService
from app.services.runtime_profile_service import RuntimeProfileService

router = APIRouter(prefix="/api/users", tags=["users"])
settings = get_settings()


def _get_user_or_404(repo: UserRepository, user_id: int):
    user = repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.post("", response_model=UserResponse)
def create_user(payload: UserCreateRequest, admin=Depends(require_admin), db: Session = Depends(get_db)):
    repo = UserRepository(db)
    username = payload.username.strip()
    if repo.get_by_username_case_insensitive(username):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")

    UserAllowlistRepository(db).ensure(
        username,
        role=payload.role,
        added_by_user_id=admin.id,
        reactivate=True,
    )
    user = repo.create(username, hash_password(payload.password), payload.role)
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


@router.get("/admin-overview", response_model=MemberOverviewResponse)
def admin_overview(_: object = Depends(require_admin), db: Session = Depends(get_db)):
    return MemberOverviewResponse.model_validate(MemberManagementService(db).build_overview())


@router.get("/{user_id}", response_model=UserResponse)
def get_user(user_id: int, _: object = Depends(require_admin), db: Session = Depends(get_db)):
    return UserResponse.model_validate(_get_user_or_404(UserRepository(db), user_id))


@router.post("/allowlist", response_model=AllowlistResponse)
def add_to_allowlist(
    payload: AllowlistCreateRequest,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    allowlist = UserAllowlistRepository(db)
    existing = allowlist.get_by_username(payload.username)
    if existing and existing.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username is already allowlisted")

    entry = allowlist.ensure(
        payload.username,
        role=payload.role,
        added_by_user_id=admin.id,
        reactivate=True,
    )
    existing_user = UserRepository(db).get_by_username_case_insensitive(payload.username)
    if existing_user and existing_user.role != payload.role:
        UserRepository(db).update_access(existing_user, role=payload.role)
    AuditRepository(db).create(
        action="allowlist_user",
        target_type="user_allowlist",
        target_id=str(entry.id),
        user_id=admin.id,
        details={"username": entry.username, "role": entry.role},
    )
    return AllowlistResponse.model_validate(entry)


@router.delete("/allowlist/{entry_id}")
def remove_from_allowlist(
    entry_id: int,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    allowlist = UserAllowlistRepository(db)
    entry = allowlist.get_by_id(entry_id)
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Allowlist entry not found")

    normalized = normalize_username(entry.username)
    if normalized == normalize_username(settings.bootstrap_admin_username):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The configured bootstrap administrator cannot be removed from the allowlist",
        )
    target_user = UserRepository(db).get_by_username_case_insensitive(entry.username)
    if target_user and target_user.id == admin.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You cannot remove your own access")
    access = AccessControlService(db)
    if target_user and access.is_effective_admin(target_user) and access.count_effective_admins() <= 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="At least one allowlisted administrator is required")

    audit_details = {"username": entry.username}
    allowlist.delete(entry)
    AuditRepository(db).create(
        action="remove_allowlist_user",
        target_type="user_allowlist",
        target_id=str(entry_id),
        user_id=admin.id,
        details=audit_details,
    )
    return {"ok": True}


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    payload: UserAdminUpdateRequest,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    repo = UserRepository(db)
    user = _get_user_or_404(repo, user_id)
    if payload.role is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No changes supplied")
    if (
        normalize_username(user.username) == normalize_username(settings.bootstrap_admin_username)
        and payload.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The configured bootstrap administrator must keep the administrator role",
        )
    if user.id == admin.id and payload.role != "admin":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You cannot remove your own administrator access")

    access = AccessControlService(db)
    loses_admin = access.is_effective_admin(user) and payload.role != "admin"
    if loses_admin and access.count_effective_admins() <= 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="At least one allowlisted administrator is required")

    updated = repo.update_access(user, role=payload.role)
    entry = UserAllowlistRepository(db).get_by_username(updated.username)
    if entry and payload.role is not None:
        UserAllowlistRepository(db).update_role(entry, payload.role)
    AuditRepository(db).create(
        action="update_user_role",
        target_type="user",
        target_id=str(updated.id),
        user_id=admin.id,
        details={"role": updated.role},
    )
    return UserResponse.model_validate(updated)


@router.put("/{user_id}", response_model=UserResponse)
def replace_user_role(
    user_id: int,
    payload: UserAdminUpdateRequest,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return update_user(user_id, payload, admin, db)


@router.delete("/{user_id}")
def revoke_user_access(
    user_id: int,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = _get_user_or_404(UserRepository(db), user_id)
    entry = UserAllowlistRepository(db).get_by_username(user.username)
    if not entry or not entry.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User is not allowlisted")
    return remove_from_allowlist(entry.id, admin, db)
