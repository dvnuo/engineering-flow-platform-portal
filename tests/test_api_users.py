from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models.user import User
from app.models.runtime_profile import RuntimeProfile


def test_admin_create_user_provisions_default_runtime_profile(monkeypatch):
    from app.main import app
    import app.api.users as users_api

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    admin = User(username="admin", password_hash="x", role="admin", is_active=True)
    db.add(admin)
    db.commit()
    db.refresh(admin)

    def _override_db():
        yield db

    def _override_admin():
        return SimpleNamespace(id=admin.id, role="admin", username=admin.username, nickname=admin.username)

    app.dependency_overrides[users_api.get_db] = _override_db
    app.dependency_overrides[users_api.require_admin] = _override_admin

    try:
        client = TestClient(app)
        resp = client.post("/api/users", json={"username": "dev", "password": "pass123", "role": "user"})
        assert resp.status_code == 200

        created = db.scalar(select(User).where(User.username == "dev"))
        assert created is not None
        profiles = list(db.scalars(select(RuntimeProfile).where(RuntimeProfile.owner_user_id == created.id)).all())
        assert len(profiles) == 1
        assert profiles[0].is_default is True
    finally:
        app.dependency_overrides.clear()
        db.close()
