from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.repositories.agent_group_member_repo import AgentGroupMemberRepository
from app.repositories.agent_group_repo import AgentGroupRepository
from app.repositories.agent_repo import AgentRepository
from app.repositories.user_repo import UserRepository
from app.schemas.agent_group import (
    AgentGroupCreateRequest,
    AgentGroupDetailResponse,
    AgentGroupMemberCreateRequest,
    AgentGroupMemberResponse,
    AgentGroupResponse,
)

router = APIRouter(tags=["agent-groups"])


@router.post("/api/agent-groups", response_model=AgentGroupDetailResponse)
def create_agent_group(payload: AgentGroupCreateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    agent_repo = AgentRepository(db)
    user_repo = UserRepository(db)
    group_repo = AgentGroupRepository(db)
    member_repo = AgentGroupMemberRepository(db)

    leader_agent = agent_repo.get_by_id(payload.leader_agent_id)
    if not leader_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leader agent not found")

    unique_user_ids = list(dict.fromkeys(payload.member_user_ids))
    unique_agent_ids = list(dict.fromkeys(payload.member_agent_ids))

    for member_user_id in unique_user_ids:
        if not user_repo.get_by_id(member_user_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"User member not found: {member_user_id}")

    for member_agent_id in unique_agent_ids:
        if not agent_repo.get_by_id(member_agent_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent member not found: {member_agent_id}")

    group = group_repo.create(
        name=payload.name,
        leader_agent_id=payload.leader_agent_id,
        shared_context_policy_json=payload.shared_context_policy_json,
        task_routing_policy_json=payload.task_routing_policy_json,
        ephemeral_agent_policy_json=payload.ephemeral_agent_policy_json,
        created_by_user_id=user.id,
    )

    member_repo.create(
        group_id=group.id,
        member_type="agent",
        agent_id=payload.leader_agent_id,
        user_id=None,
        role="leader",
    )

    for member_agent_id in unique_agent_ids:
        if member_agent_id == payload.leader_agent_id:
            continue
        if member_repo.get_by_group_and_agent(group.id, member_agent_id):
            continue
        member_repo.create(
            group_id=group.id,
            member_type="agent",
            agent_id=member_agent_id,
            user_id=None,
            role="member",
        )

    for member_user_id in unique_user_ids:
        if member_repo.get_by_group_and_user(group.id, member_user_id):
            continue
        member_repo.create(
            group_id=group.id,
            member_type="user",
            user_id=member_user_id,
            agent_id=None,
            role="member",
        )

    members = member_repo.list_by_group(group.id)
    return AgentGroupDetailResponse(
        **AgentGroupResponse.model_validate(group).model_dump(),
        members=[AgentGroupMemberResponse.model_validate(item) for item in members],
    )


@router.get("/api/agent-groups", response_model=list[AgentGroupResponse])
def list_agent_groups(user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    groups = AgentGroupRepository(db).list_all()
    return [AgentGroupResponse.model_validate(group) for group in groups]


@router.get("/api/agent-groups/{group_id}", response_model=AgentGroupDetailResponse)
def get_agent_group(group_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    group_repo = AgentGroupRepository(db)
    member_repo = AgentGroupMemberRepository(db)

    group = group_repo.get_by_id(group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    members = member_repo.list_by_group(group.id)
    return AgentGroupDetailResponse(
        **AgentGroupResponse.model_validate(group).model_dump(),
        members=[AgentGroupMemberResponse.model_validate(item) for item in members],
    )


@router.post("/api/agent-groups/{group_id}/members", response_model=AgentGroupMemberResponse)
def add_agent_group_member(
    group_id: str,
    payload: AgentGroupMemberCreateRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ = user
    group_repo = AgentGroupRepository(db)
    member_repo = AgentGroupMemberRepository(db)
    user_repo = UserRepository(db)
    agent_repo = AgentRepository(db)

    group = group_repo.get_by_id(group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    member_type = (payload.member_type or "").strip().lower()
    if member_type not in {"user", "agent"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="member_type must be 'user' or 'agent'")

    if payload.role == "leader":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group already has a leader member")

    if member_type == "user":
        if not payload.user_id or payload.agent_id is not None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user member must set user_id and omit agent_id")
        if not user_repo.get_by_id(payload.user_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User member not found")
        existing = member_repo.get_by_group_and_user(group.id, payload.user_id)
        if existing:
            return AgentGroupMemberResponse.model_validate(existing)
        member = member_repo.create(
            group_id=group.id,
            member_type="user",
            user_id=payload.user_id,
            agent_id=None,
            role=payload.role,
        )
        return AgentGroupMemberResponse.model_validate(member)

    if not payload.agent_id or payload.user_id is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="agent member must set agent_id and omit user_id")
    if not agent_repo.get_by_id(payload.agent_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent member not found")
    existing = member_repo.get_by_group_and_agent(group.id, payload.agent_id)
    if existing:
        return AgentGroupMemberResponse.model_validate(existing)

    member = member_repo.create(
        group_id=group.id,
        member_type="agent",
        user_id=None,
        agent_id=payload.agent_id,
        role=payload.role,
    )
    return AgentGroupMemberResponse.model_validate(member)


@router.delete("/api/agent-groups/{group_id}/members/{member_id}")
def delete_agent_group_member(group_id: str, member_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _ = user
    group_repo = AgentGroupRepository(db)
    member_repo = AgentGroupMemberRepository(db)

    group = group_repo.get_by_id(group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    member = member_repo.get_by_id(member_id)
    if not member or member.group_id != group_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group member not found")

    if member.role == "leader" and member.agent_id == group.leader_agent_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot remove current group leader member")

    member_repo.delete(member)
    return {"ok": True}
