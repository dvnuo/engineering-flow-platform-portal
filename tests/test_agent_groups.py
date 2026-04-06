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
    outsider = User(username="outsider-user", password_hash=hash_password("pw"), role="viewer", is_active=True)
    db.add(owner)
    db.add(user_member)
    db.add(outsider)
    db.commit()
    db.refresh(owner)
    db.refresh(user_member)
    db.refresh(outsider)

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

    state = {"user": SimpleNamespace(id=owner.id, role="admin", username=owner.username, nickname="Owner")}

    def _override_user():
        return state["user"]

    def _override_db():
        yield db

    app.dependency_overrides[groups_api.get_current_user] = _override_user
    app.dependency_overrides[groups_api.get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    def _set_user(user):
        state["user"] = SimpleNamespace(id=user.id, role=user.role, username=user.username, nickname=user.username)

    return TestClient(app), leader_agent, member_agent, user_member, outsider, _set_user, _cleanup


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
    client, leader_agent, _member_agent, user_member, _outsider, _set_user, cleanup = _build_client_with_overrides()
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
    client, leader_agent, member_agent, _user_member, _outsider, _set_user, cleanup = _build_client_with_overrides()
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
    client, leader_agent, member_agent, _user_member, _outsider, _set_user, cleanup = _build_client_with_overrides()
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
    client, leader_agent, _member_agent, _user_member, _outsider, _set_user, cleanup = _build_client_with_overrides()
    try:
        group = _create_group(client, leader_agent.id)
        leader_member = next(item for item in group["members"] if item["role"] == "leader")

        remove_resp = client.delete(f"/api/agent-groups/{group['id']}/members/{leader_member['id']}")
        assert remove_resp.status_code == 409
        assert remove_resp.json()["detail"] == "Cannot remove current group leader member"
    finally:
        cleanup()


def test_create_group_rolls_back_when_member_write_fails(monkeypatch):
    client, leader_agent, member_agent, _user_member, _outsider, _set_user, cleanup = _build_client_with_overrides()
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


def test_group_list_and_detail_enforce_view_permissions():
    client, leader_agent, _member_agent, user_member, outsider, set_user, cleanup = _build_client_with_overrides()
    try:
        group = _create_group(client, leader_agent.id)

        client.post(
            f"/api/agent-groups/{group['id']}/members",
            json={"member_type": "user", "user_id": user_member.id, "role": "member"},
        )

        set_user(user_member)
        member_list = client.get("/api/agent-groups")
        assert member_list.status_code == 200
        assert len(member_list.json()) == 1
        member_detail = client.get(f"/api/agent-groups/{group['id']}")
        assert member_detail.status_code == 200

        set_user(outsider)
        outsider_list = client.get("/api/agent-groups")
        assert outsider_list.status_code == 200
        assert outsider_list.json() == []
        outsider_detail = client.get(f"/api/agent-groups/{group['id']}")
        assert outsider_detail.status_code == 403
    finally:
        cleanup()


def test_participant_cannot_add_or_remove_members():
    client, leader_agent, member_agent, user_member, _outsider, set_user, cleanup = _build_client_with_overrides()
    try:
        group = _create_group(client, leader_agent.id)
        add_resp = client.post(
            f"/api/agent-groups/{group['id']}/members",
            json={"member_type": "agent", "agent_id": member_agent.id, "role": "member"},
        )
        member_id = add_resp.json()["id"]

        client.post(
            f"/api/agent-groups/{group['id']}/members",
            json={"member_type": "user", "user_id": user_member.id, "role": "member"},
        )
        set_user(user_member)
        participant_add = client.post(
            f"/api/agent-groups/{group['id']}/members",
            json={"member_type": "agent", "agent_id": leader_agent.id, "role": "member"},
        )
        assert participant_add.status_code == 403

        participant_remove = client.delete(f"/api/agent-groups/{group['id']}/members/{member_id}")
        assert participant_remove.status_code == 403
    finally:
        cleanup()


def test_group_leader_must_be_workspace_agent():
    client, _leader_agent, _member_agent, _user_member, _outsider, _set_user, cleanup = _build_client_with_overrides()
    try:
        from app.main import app
        import app.api.agent_groups as groups_api

        db_gen = app.dependency_overrides[groups_api.get_db]()
        db = next(db_gen)
        non_workspace = Agent(
            name="Non Workspace",
            description="non-workspace",
            owner_user_id=1,
            visibility="private",
            status="running",
            image="example/image:latest",
            repo_url="https://example.com/repo-non-workspace.git",
            branch="main",
            cpu="500m",
            memory="1Gi",
            disk_size_gi=20,
            mount_path="/root/.efp",
            namespace="efp-agents",
            deployment_name="dep-non-workspace",
            service_name="svc-non-workspace",
            pvc_name="pvc-non-workspace",
            endpoint_path="/",
            agent_type="expert",
        )
        db.add(non_workspace)
        db.commit()
        db.refresh(non_workspace)

        response = client.post(
            "/api/agent-groups",
            json={
                "name": "Invalid Leader Group",
                "leader_agent_id": non_workspace.id,
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "Leader agent must be a workspace agent"
    finally:
        cleanup()
