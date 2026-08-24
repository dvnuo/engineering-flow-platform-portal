from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentExecution, AgentTask, DelegationRule, User, UserAllowlistEntry


def _database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def _override_db(db):
    def override():
        yield db

    return override


def test_registration_requires_allowlist_and_uses_preassigned_role(monkeypatch):
    from app.main import app
    import app.api.auth as auth_api

    db = _database()
    app.dependency_overrides[auth_api.get_db] = _override_db(db)
    monkeypatch.setattr(auth_api, "hash_password", lambda value: f"hashed-{value}")
    client = TestClient(app)
    try:
        denied = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "pass123"},
        )
        assert denied.status_code == 403

        db.add(UserAllowlistEntry(username="alice", role="viewer", is_active=True))
        db.commit()
        allowed = client.post(
            "/api/auth/register",
            json={"username": "Alice", "password": "pass123"},
        )
        assert allowed.status_code == 200
        assert allowed.json()["role"] == "viewer"
        user = db.query(User).filter_by(username="Alice").one()
        assert user.last_login_at is not None
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_existing_non_allowlisted_user_cannot_login_or_keep_session(monkeypatch):
    from app.main import app
    import app.api.auth as auth_api

    db = _database()
    user = User(username="member", password_hash="hashed", role="user", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    app.dependency_overrides[auth_api.get_db] = _override_db(db)
    monkeypatch.setattr(auth_api, "verify_password", lambda _raw, _hashed: True)
    client = TestClient(app)
    try:
        denied = client.post("/api/auth/login", json={"username": "member", "password": "secret"})
        assert denied.status_code == 403

        entry = UserAllowlistEntry(username="member", role="user", is_active=True)
        db.add(entry)
        db.commit()
        allowed = client.post("/api/auth/login", json={"username": "MEMBER", "password": "secret"})
        assert allowed.status_code == 200
        assert allowed.cookies.get("portal_session")

        db.delete(entry)
        db.commit()
        revoked = client.get("/api/auth/me")
        assert revoked.status_code == 401
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_revoked_session_stays_on_unauthorized_page_until_access_is_restored(monkeypatch):
    from app.main import app
    import app.web as web_module
    from app.services.auth_service import issue_session_token

    db = _database()
    user = User(username="revoked-member", password_hash="hash", role="user", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: db)
    client = TestClient(app)
    client.cookies.set(web_module.settings.session_cookie_name, issue_session_token(user.id))

    try:
        denied = client.get("/app", follow_redirects=False)
        assert denied.status_code == 302
        assert denied.headers["location"] == "/unauthorized"
        assert "Max-Age=0" not in denied.headers.get("set-cookie", "")

        unauthorized = client.get(denied.headers["location"], follow_redirects=False)
        assert unauthorized.status_code == 403
        assert "Authorization required" in unauthorized.text
        assert "Contact an administrator" in unauthorized.text
        assert 'id="refresh-access-btn"' in unauthorized.text
        assert 'id="unauthorized-logout-btn"' in unauthorized.text
        assert "@keyframes portal-access" in Path("app/static/css/app.css").read_text(encoding="utf-8")

        db.add(UserAllowlistEntry(username=user.username, role="user", is_active=True))
        db.commit()
        refreshed = client.get("/unauthorized", follow_redirects=False)
        assert refreshed.status_code == 302
        assert refreshed.headers["location"] == "/app"
    finally:
        db.close()


def test_login_page_clears_invalid_cookie_without_redirect():
    from app.main import app
    import app.web as web_module

    client = TestClient(app)
    client.cookies.set(web_module.settings.session_cookie_name, "invalid-session")

    response = client.get("/login", follow_redirects=False)
    assert response.status_code == 200
    assert "location" not in response.headers
    set_cookie = response.headers.get("set-cookie", "")
    assert f'{web_module.settings.session_cookie_name}=""' in set_cookie
    assert "Max-Age=0" in set_cookie


def test_inactive_session_uses_unauthorized_page(monkeypatch):
    from app.main import app
    import app.web as web_module
    from app.services.auth_service import issue_session_token

    db = _database()
    user = User(username="inactive-member", password_hash="hash", role="user", is_active=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(UserAllowlistEntry(username=user.username, role="user", is_active=True))
    db.commit()
    monkeypatch.setattr(web_module, "SessionLocal", lambda: db)
    client = TestClient(app)
    client.cookies.set(web_module.settings.session_cookie_name, issue_session_token(user.id))

    try:
        denied = client.get("/app", follow_redirects=False)
        assert denied.status_code == 302
        assert denied.headers["location"] == "/unauthorized"
        page = client.get("/unauthorized", follow_redirects=False)
        assert page.status_code == 403
        assert "currently inactive" in page.text
    finally:
        db.close()


def test_admin_can_manage_allowlist_role_and_status(monkeypatch):
    from app.main import app
    import app.api.users as users_api
    import app.deps as deps_module

    db = _database()
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    member = User(username="member", password_hash="hash", role="user", is_active=True)
    db.add_all([admin, member])
    db.commit()
    db.add(UserAllowlistEntry(username="admin", role="admin", is_active=True))
    db.commit()

    current = SimpleNamespace(
        id=admin.id,
        username=admin.username,
        nickname=admin.username,
        role=admin.role,
        is_active=True,
    )
    app.dependency_overrides[users_api.get_db] = _override_db(db)
    app.dependency_overrides[deps_module.require_admin] = lambda: current
    app.dependency_overrides[deps_module.get_current_user] = lambda: current
    monkeypatch.setattr(users_api.settings, "bootstrap_admin_username", "admin")
    client = TestClient(app)
    try:
        allow = client.post(
            "/api/users/allowlist",
            json={"username": "member", "role": "viewer"},
        )
        assert allow.status_code == 200
        db.refresh(member)
        assert member.role == "viewer"

        update = client.patch(
            f"/api/users/{member.id}",
            json={"role": "admin", "is_active": False},
        )
        assert update.status_code == 200
        assert update.json()["role"] == "admin"
        assert update.json()["is_active"] is False

        overview = client.get("/api/users/admin-overview")
        assert overview.status_code == 200
        assert overview.json()["summary"]["total_users"] == 2

        self_lockout = client.patch(
            f"/api/users/{admin.id}",
            json={"role": "user"},
        )
        assert self_lockout.status_code == 409

        admin_entry = db.query(UserAllowlistEntry).filter_by(username="admin").one()
        protected = client.delete(f"/api/users/allowlist/{admin_entry.id}")
        assert protected.status_code == 409
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_member_overview_aggregates_portal_usage():
    from app.services.member_management_service import MemberManagementService

    db = _database()
    user = User(username="usage-user", password_hash="hash", role="user", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(UserAllowlistEntry(username=user.username, role="user", is_active=True))
    agent = Agent(
        name="Usage agent",
        owner_user_id=user.id,
        visibility="private",
        status="running",
        image="example/agent:latest",
        deployment_name="usage-agent",
        service_name="usage-agent",
        pvc_name="usage-agent",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    db.add_all(
        [
            AgentTask(
                assignee_agent_id=agent.id,
                source="manual",
                task_type="general",
                status="done",
                owner_user_id=user.id,
                created_by_user_id=user.id,
            ),
            AgentExecution(
                agent_id=agent.id,
                kind="chat",
                status="succeeded",
                owner_user_id=user.id,
                created_by_user_id=user.id,
            ),
            DelegationRule(
                name="Usage delegation",
                source_type="timer",
                trigger_type="timer",
                target_agent_id=agent.id,
                task_type="general",
                owner_user_id=user.id,
                created_by_user_id=user.id,
            ),
        ]
    )
    db.commit()
    try:
        overview = MemberManagementService(db).build_overview()
        row = overview["users"][0]
        assert row["assistant_count"] == 1
        assert row["task_count"] == 1
        assert row["completed_task_count"] == 1
        assert row["execution_count"] == 1
        assert row["chat_count"] == 1
        assert row["delegation_count"] == 1
        assert row["last_activity_at"] is not None
    finally:
        db.close()


def test_configured_access_creates_first_admin_and_seeds_allowlist(monkeypatch):
    from app.config import Settings
    import app.services.access_control_service as access_module

    db = _database()
    monkeypatch.setattr(access_module, "hash_password", lambda value: f"hashed-{value}")
    settings = Settings(
        BOOTSTRAP_ADMIN_USERNAME="root-admin",
        BOOTSTRAP_ADMIN_PASSWORD="strong-password",
        PORTAL_USER_ALLOWLIST="alice; bob\nALICE",
    )
    try:
        admin = access_module.AccessControlService(db).ensure_configured_access(settings)
        assert admin.username == "root-admin"
        assert admin.role == "admin"
        assert admin.is_active is True
        entries = {row.username: row.role for row in db.query(UserAllowlistEntry).all()}
        assert entries == {"alice": "user", "bob": "user", "root-admin": "admin"}
    finally:
        db.close()


def test_configured_admin_promotion_rotates_claimed_account_password(monkeypatch):
    from app.config import Settings
    import app.services.access_control_service as access_module

    db = _database()
    claimed = User(username="configured-admin", password_hash="old-hash", role="user", is_active=True)
    db.add(claimed)
    db.commit()
    monkeypatch.setattr(access_module, "hash_password", lambda value: f"hashed-{value}")
    settings = Settings(
        BOOTSTRAP_ADMIN_USERNAME="configured-admin",
        BOOTSTRAP_ADMIN_PASSWORD="controlled-password",
    )
    try:
        admin = access_module.AccessControlService(db).ensure_configured_access(settings)
        assert admin.role == "admin"
        assert admin.password_hash == "hashed-controlled-password"
        assert db.query(UserAllowlistEntry).filter_by(username="configured-admin").one().role == "admin"
    finally:
        db.close()


def test_users_panel_contains_management_and_usage_controls(monkeypatch):
    from app.main import app
    import app.web as web_module

    db = _database()
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    db.add(admin)
    db.commit()
    db.refresh(admin)
    db.add(UserAllowlistEntry(username="admin", role="admin", is_active=True))
    db.commit()
    monkeypatch.setattr(web_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: admin)
    client = TestClient(app)
    try:
        response = client.get("/app/users/panel")
        assert response.status_code == 200
        assert "Member administration" in response.text
        assert "data-admin-allowlist-form" in response.text
        assert "Executions" in response.text
        assert "data-admin-member-form" in response.text
        assert "Reset password" not in response.text
        assert "data-admin-password-form" not in response.text
        assert not any(
            getattr(route, "path", "") == "/api/users/{user_id}/password"
            for route in app.routes
        )
        admin_js = Path("app/static/js/admin_users.js").read_text(encoding="utf-8")
        assert "/password" not in admin_js
    finally:
        db.close()
