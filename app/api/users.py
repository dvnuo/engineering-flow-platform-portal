from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.i18n import N_
from app.config import get_settings
from app.db import SessionLocal, get_db
from app.deps import get_current_user, require_admin, require_administrator
from app.repositories.audit_repo import AuditRepository
from app.repositories.user_allowlist_repo import UserAllowlistRepository, normalize_username
from app.repositories.user_repo import UserRepository
from app.schemas.user import (
    AllowlistBulkCreateRequest,
    AllowlistBulkResponse,
    AllowlistCreateRequest,
    AllowlistResponse,
    MemberOverviewResponse,
    UserAdminUpdateRequest,
    UserCreateRequest,
    UserResponse,
)
from app.services.access_control_service import AccessControlService
from app.services.auth_service import hash_password
from app.services.authorization import (
    ROLE_SYSTEM_ADMIN,
    assignable_roles,
    can_manage_member,
    is_system_admin,
)
from app.services.connector_defaults_sync import rollout_profiles
from app.services.group_service import GroupService
from app.services.member_management_service import DEFAULT_PERIOD_DAYS, PERIOD_DAYS_OPTIONS, MemberManagementService
from app.services.runtime_profile_secret_service import RuntimeProfileSecretService
from app.services.runtime_profile_service import RuntimeProfileService

router = APIRouter(prefix="/api/users", tags=["users"])
settings = get_settings()
runtime_profile_secret_service = RuntimeProfileSecretService()


def _get_user_or_404(repo: UserRepository, user_id: int):
    user = repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=N_("User not found"))
    return user


def _schedule_rollout(background_tasks: BackgroundTasks, profile_ids: list[str], actor_id: int, reason: str) -> None:
    """Secret and restarts for members whose settings a change of group moved, after the response."""

    ids = [item for item in profile_ids if item]
    if ids:
        background_tasks.add_task(
            rollout_profiles,
            ids,
            secret_service=runtime_profile_secret_service,
            triggered_by_user_id=actor_id,
            session_factory=SessionLocal,
            reason=reason,
        )


def _target_group(actor, requested_group_id, groups: GroupService):
    """The group an allowlist entry made by ``actor`` joins.

    A system administrator names it; a group administrator's is their own.
    """

    if is_system_admin(actor):
        if requested_group_id is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=N_("Choose a group for the new members"))
        group = groups.repo.get_by_id(requested_group_id)
        if group is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=N_("Group not found"))
        return group
    own = groups.group_of(actor)
    if requested_group_id is not None and requested_group_id != own.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=N_("You can only add members to your own group"))
    return own


def _check_role_assignable(actor, role: str) -> None:
    if role not in assignable_roles(actor):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=N_("You cannot assign this role"))


def _claim_refusal(actor, username: str, group, groups: GroupService, db: Session) -> str | None:
    """Why ``actor`` may not put ``username`` in ``group``, or None.

    A group administrator adds new people, and accounts nobody has
    allowlisted yet (they sit in the default group); someone already in
    another group, or a system administrator, is a system administrator's call.
    """

    if is_system_admin(actor):
        return None
    user = UserRepository(db).get_by_username_case_insensitive(username)
    entry = UserAllowlistRepository(db).get_by_username(username)
    if user is not None and user.role == ROLE_SYSTEM_ADMIN:
        return N_("A system administrator's access can only be changed by a system administrator")
    default_id = groups.default_group_id()
    if user is not None:
        current = groups.find_group_of(user)
        current_id = current.id if current is not None else None
    elif entry is not None:
        current_id = entry.group_id if entry.group_id is not None else default_id
    else:
        return None
    if current_id is None or current_id == group.id:
        return None
    unassigned = current_id == default_id and not (entry is not None and entry.is_active)
    if unassigned:
        return None
    return N_("This member belongs to another group. Ask a system administrator to move them.")


def _ensure_allowlist_access(
    username: str,
    role: str,
    *,
    admin_id: int,
    db: Session,
    group=None,
):
    """Allowlist ``username`` with ``role`` in ``group``; returns ``(entry, profile id to roll out or None)``.

    An account that already exists takes the role, and changes group the way
    a move does (``GroupService.move_member``).
    """

    entry = UserAllowlistRepository(db).ensure(
        username,
        role=role,
        added_by_user_id=admin_id,
        reactivate=True,
    )
    profile_id = None
    existing_user = UserRepository(db).get_by_username_case_insensitive(username)
    if group is not None:
        if existing_user is not None:
            profile_id = GroupService(db).move_member(existing_user, group, actor_id=admin_id).profile_id
            db.refresh(entry)
        if entry.group_id != group.id:
            entry.group_id = group.id
            db.add(entry)
            db.commit()
            db.refresh(entry)
    if existing_user and existing_user.role != role:
        UserRepository(db).update_access(existing_user, role=role)
    if entry.role != role:
        UserAllowlistRepository(db).update_role(entry, role)
    return entry, profile_id


def _visible_member_or_404(actor, user, groups: GroupService):
    """``user`` when ``actor`` administers their group; 404 otherwise, so a group administrator learns nothing of other groups."""

    if is_system_admin(actor):
        return user
    group = groups.find_group_of(user)
    if group is None or group.id != groups.group_of(actor).id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=N_("User not found"))
    return user


@router.post("", response_model=UserResponse)
def create_user(payload: UserCreateRequest, admin=Depends(require_admin), db: Session = Depends(get_db)):
    repo = UserRepository(db)
    username = payload.username
    if repo.get_by_username_case_insensitive(username):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=N_("Username already exists"))
    group = _target_group(admin, payload.group_id, GroupService(db))

    entry = UserAllowlistRepository(db).ensure(
        username,
        role=payload.role,
        added_by_user_id=admin.id,
        reactivate=True,
    )
    entry.group_id = group.id
    db.add(entry)
    db.commit()
    user = repo.create(username, hash_password(payload.password), payload.role)
    user.group_id = group.id
    db.add(user)
    db.commit()
    db.refresh(user)
    RuntimeProfileService(db).ensure_user_has_default_profile(user)
    AuditRepository(db).create(
        action="create_user",
        target_type="user",
        target_id=str(user.id),
        user_id=admin.id,
        details={"username": user.username, "role": user.role, "group": group.name},
    )
    return UserResponse.model_validate(user)


@router.get("", response_model=list[UserResponse])
def list_users(actor=Depends(require_administrator), db: Session = Depends(get_db)):
    users = UserRepository(db).list_all()
    if not is_system_admin(actor):
        groups = GroupService(db)
        own_id = groups.group_of(actor).id
        users = [user for user in users if getattr(groups.find_group_of(user), "id", None) == own_id]
    return [UserResponse.model_validate(user) for user in users]


@router.get("/admin-overview", response_model=MemberOverviewResponse)
def admin_overview(
    actor=Depends(require_administrator),
    db: Session = Depends(get_db),
    days: int = Query(default=DEFAULT_PERIOD_DAYS, description="Period of the consumption columns: one of 7, 30, 90 days."),
):
    if days not in PERIOD_DAYS_OPTIONS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=N_("days must be one of 7, 30 or 90"))
    group_id = None if is_system_admin(actor) else GroupService(db).group_of(actor).id
    return MemberOverviewResponse.model_validate(MemberManagementService(db).build_overview(days=days, group_id=group_id))


@router.get("/me/usage")
def my_usage(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
    days: int = Query(default=DEFAULT_PERIOD_DAYS, description="Period of the figures: one of 7, 30, 90 days."),
):
    """The signed-in member's own runs across their assistants, this month's credits and the guideline notice."""
    if days not in PERIOD_DAYS_OPTIONS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=N_("days must be one of 7, 30 or 90"))
    service = MemberManagementService(db)
    usage = service.member_usage(user.id, days=days)
    usage["notice"] = service.credit_notice(user.id)
    return usage


@router.get("/{user_id}/usage")
def member_usage(
    user_id: int,
    actor=Depends(require_administrator),
    db: Session = Depends(get_db),
    days: int = Query(default=DEFAULT_PERIOD_DAYS, description="Period of the figures: one of 7, 30, 90 days."),
):
    """One member's consumption over the period: daily credits, models, kinds of run (the User Management drawer)."""
    if days not in PERIOD_DAYS_OPTIONS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=N_("days must be one of 7, 30 or 90"))
    user = _visible_member_or_404(actor, _get_user_or_404(UserRepository(db), user_id), GroupService(db))
    usage = MemberManagementService(db).member_usage(user.id, days=days)
    usage["username"] = user.username
    usage["display_name"] = (user.nickname or user.username or "").strip()
    return usage


@router.get("/{user_id}", response_model=UserResponse)
def get_user(user_id: int, actor=Depends(require_administrator), db: Session = Depends(get_db)):
    user = _visible_member_or_404(actor, _get_user_or_404(UserRepository(db), user_id), GroupService(db))
    return UserResponse.model_validate(user)


@router.post("/allowlist", response_model=AllowlistResponse)
def add_to_allowlist(
    payload: AllowlistCreateRequest,
    background_tasks: BackgroundTasks,
    admin=Depends(require_administrator),
    db: Session = Depends(get_db),
):
    groups = GroupService(db)
    group = _target_group(admin, payload.group_id, groups)
    _check_role_assignable(admin, payload.role)
    allowlist = UserAllowlistRepository(db)
    existing = allowlist.get_by_username(payload.username)
    if existing and existing.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=N_("Username is already allowlisted"))
    refusal = _claim_refusal(admin, payload.username, group, groups, db)
    if refusal:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal)

    entry, profile_id = _ensure_allowlist_access(
        payload.username,
        payload.role,
        admin_id=admin.id,
        db=db,
        group=group,
    )
    AuditRepository(db).create(
        action="allowlist_user",
        target_type="user_allowlist",
        target_id=str(entry.id),
        user_id=admin.id,
        details={"username": entry.username, "role": entry.role, "group": group.name},
    )
    _schedule_rollout(background_tasks, [profile_id], admin.id, "allowlisted_into_group")
    return AllowlistResponse.model_validate(entry)


@router.post("/allowlist/bulk", response_model=AllowlistBulkResponse)
def add_bulk_to_allowlist(
    payload: AllowlistBulkCreateRequest,
    background_tasks: BackgroundTasks,
    admin=Depends(require_administrator),
    db: Session = Depends(get_db),
):
    groups = GroupService(db)
    group = _target_group(admin, payload.group_id, groups)
    _check_role_assignable(admin, payload.role)
    allowlist = UserAllowlistRepository(db)
    added: list[str] = []
    already_allowlisted: list[str] = []
    refused: list[str] = []
    profile_ids: list[str] = []

    for username in payload.usernames:
        existing = allowlist.get_by_username(username)
        if existing and existing.is_active:
            already_allowlisted.append(username)
            continue
        if _claim_refusal(admin, username, group, groups, db):
            refused.append(username)
            continue
        entry, profile_id = _ensure_allowlist_access(
            username,
            payload.role,
            admin_id=admin.id,
            db=db,
            group=group,
        )
        added.append(entry.username)
        profile_ids.append(profile_id)

    AuditRepository(db).create(
        action="bulk_allowlist_users",
        target_type="user_allowlist",
        target_id="bulk",
        user_id=admin.id,
        details={
            "role": payload.role,
            "group": group.name,
            "added": added,
            "already_allowlisted": already_allowlisted,
            "refused": refused,
        },
    )
    _schedule_rollout(background_tasks, profile_ids, admin.id, "allowlisted_into_group")
    return AllowlistBulkResponse(added=added, already_allowlisted=already_allowlisted, refused=refused)


@router.delete("/allowlist/{entry_id}")
def remove_from_allowlist(
    entry_id: int,
    admin=Depends(require_administrator),
    db: Session = Depends(get_db),
):
    allowlist = UserAllowlistRepository(db)
    entry = allowlist.get_by_id(entry_id)
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=N_("Allowlist entry not found"))

    normalized = normalize_username(entry.username)
    if normalized == normalize_username(settings.bootstrap_admin_username):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=N_("The configured bootstrap administrator cannot be removed from the allowlist"),
        )
    target_user = UserRepository(db).get_by_username_case_insensitive(entry.username)
    if target_user and target_user.id == admin.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=N_("You cannot remove your own access"))
    if not is_system_admin(admin):
        groups = GroupService(db)
        default_id = groups.default_group_id()
        target_role = target_user.role if target_user is not None else entry.role
        if target_user is not None:
            found = groups.find_group_of(target_user)
            target_group_id = found.id if found is not None else None
        else:
            target_group_id = entry.group_id if entry.group_id is not None else default_id
        if not can_manage_member(admin, target_role, target_group_id, default_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=N_("You can only manage members of your own group"))
    access = AccessControlService(db)
    if target_user and access.is_effective_admin(target_user) and access.count_effective_admins() <= 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=N_("At least one allowlisted administrator is required"))

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
    background_tasks: BackgroundTasks,
    admin=Depends(require_administrator),
    db: Session = Depends(get_db),
):
    """Change a member's role, group, or both.

    A system administrator may give any role and move anyone to any group.
    A group administrator may make members of their own group members or
    group administrators, and nothing else. Moving a member rolls their
    changed settings out after the response.
    """
    repo = UserRepository(db)
    groups = GroupService(db)
    user = _visible_member_or_404(admin, _get_user_or_404(repo, user_id), groups)
    if payload.role is None and payload.group_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=N_("No changes supplied"))

    target_group = None
    if payload.group_id is not None:
        if not is_system_admin(admin):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=N_("Only a system administrator can move members between groups"))
        target_group = groups.repo.get_by_id(payload.group_id)
        if target_group is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=N_("Group not found"))

    if payload.role is not None:
        if not is_system_admin(admin):
            current = groups.find_group_of(user)
            if not can_manage_member(admin, user.role, current.id if current else None, groups.default_group_id()):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=N_("You can only manage members of your own group"))
            _check_role_assignable(admin, payload.role)
        if (
            normalize_username(user.username) == normalize_username(settings.bootstrap_admin_username)
            and payload.role != ROLE_SYSTEM_ADMIN
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=N_("The configured bootstrap administrator must keep the administrator role"),
            )
        if user.id == admin.id and payload.role != user.role:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=N_("You cannot remove your own administrator access"))

        access = AccessControlService(db)
        loses_admin = access.is_effective_admin(user) and payload.role != ROLE_SYSTEM_ADMIN
        if loses_admin and access.count_effective_admins() <= 1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=N_("At least one allowlisted administrator is required"))

    profile_ids: list[str] = []
    if target_group is not None:
        result = groups.move_member(user, target_group, actor_id=admin.id)
        profile_ids.append(result.profile_id)
        db.refresh(user)

    updated = user
    if payload.role is not None and payload.role != user.role:
        updated = repo.update_access(user, role=payload.role)
        entry = UserAllowlistRepository(db).get_by_username(updated.username)
        if entry:
            UserAllowlistRepository(db).update_role(entry, payload.role)
        AuditRepository(db).create(
            action="update_user_role",
            target_type="user",
            target_id=str(updated.id),
            user_id=admin.id,
            details={"role": updated.role},
        )
    _schedule_rollout(background_tasks, profile_ids, admin.id, "moved_group")
    return UserResponse.model_validate(updated)


@router.put("/{user_id}", response_model=UserResponse)
def replace_user_role(
    user_id: int,
    payload: UserAdminUpdateRequest,
    background_tasks: BackgroundTasks,
    admin=Depends(require_administrator),
    db: Session = Depends(get_db),
):
    return update_user(user_id, payload, background_tasks, admin, db)


@router.delete("/{user_id}")
def revoke_user_access(
    user_id: int,
    admin=Depends(require_administrator),
    db: Session = Depends(get_db),
):
    user = _visible_member_or_404(admin, _get_user_or_404(UserRepository(db), user_id), GroupService(db))
    entry = UserAllowlistRepository(db).get_by_username(user.username)
    if not entry or not entry.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=N_("User is not allowlisted"))
    return remove_from_allowlist(entry.id, admin, db)
