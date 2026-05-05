from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.services.agent_group_service import AgentGroupService


def _service():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(bind=engine, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    svc = AgentGroupService(db)
    return db, svc


def test_runtime_source_overlay_snapshot_disabled_ignores_default_agent_repo_url():
    db, svc = _service()
    try:
        svc.settings.enable_runtime_source_overlay = False
        svc.settings.default_agent_repo_url = 'https://github.com/acme/legacy.git'
        svc.settings.default_agent_runtime_repo_url = ''
        repo, branch = svc._runtime_source_overlay_snapshot()
        assert repo is None
        assert branch is None
    finally:
        db.close()


def test_runtime_source_overlay_snapshot_enabled_with_runtime_repo():
    db, svc = _service()
    try:
        svc.settings.enable_runtime_source_overlay = True
        svc.settings.default_agent_runtime_repo_url = 'https://github.com/acme/runtime.git'
        svc.settings.default_agent_runtime_branch = 'main'
        repo, branch = svc._runtime_source_overlay_snapshot()
        assert repo == 'https://github.com/acme/runtime.git'
        assert branch == 'main'
    finally:
        db.close()
