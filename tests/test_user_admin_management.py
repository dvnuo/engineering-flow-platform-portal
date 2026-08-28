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

        invalid = client.post(
            "/api/auth/register",
            json={"username": "  a  ", "password": "pass123"},
        )
        assert invalid.status_code == 422

        db.add(UserAllowlistEntry(username="alice", role="admin", is_active=True))
        db.commit()
        allowed = client.post(
            "/api/auth/register",
            json={"username": " Alice ", "password": "pass123"},
        )
        assert allowed.status_code == 200
        assert allowed.json()["role"] == "admin"
        user = db.query(User).filter_by(username="alice").one()
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


def test_legacy_inactive_flag_does_not_override_allowlist_access(monkeypatch):
    from app.main import app
    import app.api.auth as auth_api
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
    monkeypatch.setattr(auth_api, "verify_password", lambda _raw, _hashed: True)
    app.dependency_overrides[auth_api.get_db] = _override_db(db)
    client = TestClient(app)
    client.cookies.set(web_module.settings.session_cookie_name, issue_session_token(user.id))

    try:
        allowed = client.get("/app", follow_redirects=False)
        assert allowed.status_code == 200
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        login = client.post("/api/auth/login", json={"username": user.username, "password": "secret"})
        assert login.status_code == 200
        unauthorized = client.get("/unauthorized", follow_redirects=False)
        assert unauthorized.status_code == 302
        assert unauthorized.headers["location"] == "/app"
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_admin_can_manage_allowlist_and_role(monkeypatch):
    from app.main import app
    import app.api.users as users_api
    import app.deps as deps_module

    db = _database()
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    member = User(username="member", password_hash="hash", role="admin", is_active=True)
    db.add_all([admin, member])
    db.commit()
    db.add(UserAllowlistEntry(username="admin", role="admin", is_active=True))
    db.commit()

    current = SimpleNamespace(
        id=admin.id,
        username=admin.username,
        nickname=admin.username,
        role=admin.role,
    )
    app.dependency_overrides[users_api.get_db] = _override_db(db)
    app.dependency_overrides[deps_module.require_admin] = lambda: current
    app.dependency_overrides[deps_module.get_current_user] = lambda: current
    monkeypatch.setattr(users_api.settings, "bootstrap_admin_username", "admin")
    client = TestClient(app)
    try:
        allow = client.post(
            "/api/users/allowlist",
            json={"username": " MEMBER ", "role": "user"},
        )
        assert allow.status_code == 200
        assert allow.json()["username"] == "member"
        db.refresh(member)
        assert member.role == "user"

        bulk_allow = client.post(
            "/api/users/allowlist/bulk",
            json={
                "usernames": [" Alice ", "bob", "ALICE", "admin"],
                "role": "user",
            },
        )
        assert bulk_allow.status_code == 200
        assert bulk_allow.json() == {
            "added": ["alice", "bob"],
            "already_allowlisted": ["admin"],
        }
        assert {
            row.username
            for row in db.query(UserAllowlistEntry).filter(UserAllowlistEntry.is_active.is_(True)).all()
        } == {"admin", "member", "alice", "bob"}

        update = client.patch(
            f"/api/users/{member.id}",
            json={"role": "admin"},
        )
        assert update.status_code == 200
        assert update.json()["role"] == "admin"
        assert "is_active" not in update.json()

        removed_field = client.patch(
            f"/api/users/{member.id}",
            json={"is_active": False},
        )
        assert removed_field.status_code == 422

        overview = client.get("/api/users/admin-overview")
        assert overview.status_code == 200
        assert overview.json()["summary"]["total_users"] == 2
        assert "active_users" not in overview.json()["summary"]

        self_lockout = client.patch(
            f"/api/users/{admin.id}",
            json={"role": "user"},
        )
        assert self_lockout.status_code == 409

        admin_entry = db.query(UserAllowlistEntry).filter_by(username="admin").one()
        protected = client.delete(f"/api/users/allowlist/{admin_entry.id}")
        assert protected.status_code == 409

        revoked = client.delete(f"/api/users/{member.id}")
        assert revoked.status_code == 200
        db.refresh(member)
        assert member.is_active is True
        assert db.query(UserAllowlistEntry).filter_by(username="member").one_or_none() is None
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
        assert "is_active" not in row
        assert "active_users" not in overview["summary"]
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
    blocked_member = User(username="blocked-member", password_hash="hash", role="user", is_active=True)
    db.add_all([admin, blocked_member])
    db.commit()
    db.refresh(admin)
    db.add_all(
        [
            UserAllowlistEntry(username="admin", role="admin", is_active=True),
            UserAllowlistEntry(username="invited-member", role="user", is_active=True),
        ]
    )
    db.commit()
    monkeypatch.setattr(web_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: admin)
    client = TestClient(app)
    try:
        response = client.get("/app/users/panel")
        assert response.status_code == 200
        assert "Member administration" in response.text
        assert "data-admin-allowlist-form" in response.text
        assert 'id="admin-allowlist-modal" class="modal hidden"' in response.text
        assert "data-close-admin-allowlist-modal" in response.text
        assert "portal-admin-allowlist-section" not in response.text
        assert 'textarea class="portal-form-input" name="usernames"' in response.text
        assert "data-admin-member-search" in response.text
        assert "data-admin-member-access-filter" in response.text
        assert "Executions" in response.text
        assert "data-admin-member-form" not in response.text
        assert "data-admin-role-group" in response.text
        assert "data-admin-role-option" in response.text
        assert "data-allow-member" in response.text
        assert "<details" in response.text
        assert response.text.index("Registered members") < response.text.index("Pending registration")
        assert "Account active" not in response.text
        assert 'name="is_active"' not in response.text
        assert ">Active</span>" not in response.text
        assert "Reset password" not in response.text
        assert "data-admin-password-form" not in response.text
        assert not any(
            getattr(route, "path", "") == "/api/users/{user_id}/password"
            for route in app.routes
        )
        admin_js = Path("app/static/js/admin_users.js").read_text(encoding="utf-8")
        app_template = Path("app/templates/app.html").read_text(encoding="utf-8")
        chat_ui_js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
        assert 'id="header-add-allowlist-btn"' in app_template
        assert "data-open-admin-allowlist-modal" in app_template
        assert "headerAddAllowlistBtn" in chat_ui_js
        assert 'classList.toggle("hidden", !userManagementMode)' in chat_ui_js
        assert "openAllowlistModal" in admin_js
        assert "closeAllowlistModal" in admin_js
        assert "window.showToast(successMessage" in admin_js
        assert "/api/users/allowlist/bulk" in admin_js
        assert 'document.addEventListener("change"' in admin_js
        assert "/password" not in admin_js
        assert "is_active" not in admin_js
    finally:
        db.close()
