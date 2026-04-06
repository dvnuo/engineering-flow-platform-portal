from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.services.auth_service import hash_password


def _build_client_with_overrides():
    from app.main import app
    import app.api.agent_groups as groups_api

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    owner = User(username="owner", password_hash=hash_password("pw"), role="admin", is_active=True)
    user_member = User(username="member-user", password_hash=hash_password("pw"), role="viewer", is_active=True)
    db.add(owner)
    db.add(user_member)
    db.commit()
    db.refresh(owner)
    db.refresh(user_member)

    leader_agent = Agent(
        name="Leader Agent",
        description="leader",
        owner_user_id=owner.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url="https://example.com/repo.git",
        branch="main",
        cpu="500m",
        memory="1Gi",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep-leader",
        service_name="svc-leader",
        pvc_name="pvc-leader",
        endpoint_path="/",
        agent_type="workspace",
    )
    member_agent = Agent(
        name="Member Agent",
        description="member",
        owner_user_id=owner.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url="https://example.com/repo2.git",
        branch="main",
        cpu="500m",
        memory="1Gi",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep-member",
        service_name="svc-member",
        pvc_name="pvc-member",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(leader_agent)
    db.add(member_agent)
    db.commit()
    db.refresh(leader_agent)
    db.refresh(member_agent)

    def _override_user():
        return SimpleNamespace(id=owner.id, role="admin", username=owner.username, nickname="Owner")

    def _override_db():
        yield db

    app.dependency_overrides[groups_api.get_current_user] = _override_user
    app.dependency_overrides[groups_api.get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), leader_agent, member_agent, user_member, _cleanup


def _create_group(client: TestClient, leader_agent_id: str):
    response = client.post(
        "/api/agent-groups",
        json={
            "name": "Control Group",
            "leader_agent_id": leader_agent_id,
        },
    )
    assert response.status_code == 200
    return response.json()


def test_add_user_member():
    client, leader_agent, _member_agent, user_member, cleanup = _build_client_with_overrides()
    try:
        group = _create_group(client, leader_agent.id)

        add_resp = client.post(
            f"/api/agent-groups/{group['id']}/members",
            json={"member_type": "user", "user_id": user_member.id, "role": "member"},
        )
        assert add_resp.status_code == 200
        body = add_resp.json()
        assert body["member_type"] == "user"
        assert body["user_id"] == user_member.id
    finally:
        cleanup()


def test_add_agent_member():
    client, leader_agent, member_agent, _user_member, cleanup = _build_client_with_overrides()
    try:
        group = _create_group(client, leader_agent.id)

        add_resp = client.post(
            f"/api/agent-groups/{group['id']}/members",
            json={"member_type": "agent", "agent_id": member_agent.id, "role": "specialist"},
        )
        assert add_resp.status_code == 200
        body = add_resp.json()
        assert body["member_type"] == "agent"
        assert body["agent_id"] == member_agent.id
        assert body["role"] == "specialist"
    finally:
        cleanup()


def test_remove_non_leader_member():
    client, leader_agent, member_agent, _user_member, cleanup = _build_client_with_overrides()
    try:
        group = _create_group(client, leader_agent.id)

        add_resp = client.post(
            f"/api/agent-groups/{group['id']}/members",
            json={"member_type": "agent", "agent_id": member_agent.id, "role": "member"},
        )
        assert add_resp.status_code == 200
        member_id = add_resp.json()["id"]

        remove_resp = client.delete(f"/api/agent-groups/{group['id']}/members/{member_id}")
        assert remove_resp.status_code == 200
        assert remove_resp.json()["ok"] is True
    finally:
        cleanup()


def test_remove_leader_member_is_rejected():
    client, leader_agent, _member_agent, _user_member, cleanup = _build_client_with_overrides()
    try:
        group = _create_group(client, leader_agent.id)
        leader_member = next(item for item in group["members"] if item["role"] == "leader")

        remove_resp = client.delete(f"/api/agent-groups/{group['id']}/members/{leader_member['id']}")
        assert remove_resp.status_code == 409
        assert remove_resp.json()["detail"] == "Cannot remove current group leader member"
    finally:
        cleanup()


def test_create_group_rolls_back_when_member_write_fails(monkeypatch):
    client, leader_agent, member_agent, _user_member, cleanup = _build_client_with_overrides()
    try:
        from app.repositories.agent_group_member_repo import AgentGroupMemberRepository

        original_create_no_commit = AgentGroupMemberRepository.create_no_commit

        def _boom_after_leader(self, **kwargs):
            role = kwargs.get("role")
            if role != "leader":
                raise RuntimeError("simulated member insert failure")
            return original_create_no_commit(self, **kwargs)

        monkeypatch.setattr(AgentGroupMemberRepository, "create_no_commit", _boom_after_leader)

        response = client.post(
            "/api/agent-groups",
            json={
                "name": "Broken Group",
                "leader_agent_id": leader_agent.id,
                "member_agent_ids": [member_agent.id],
            },
        )
        assert response.status_code == 400

        list_resp = client.get("/api/agent-groups")
        assert list_resp.status_code == 200
        assert list_resp.json() == []
    finally:
        cleanup()
