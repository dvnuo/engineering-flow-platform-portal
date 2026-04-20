import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, RuntimeProfile, User
from app.services.provider_config_resolver import ProviderConfigResolverError, resolve_github_for_agent


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal()


def _mk_agent(user_id: int, runtime_profile_id: str | None = None):
    return Agent(
        name="a",
        owner_user_id=user_id,
        visibility="private",
        status="running",
        image="example/image:latest",
        runtime_profile_id=runtime_profile_id,
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp",
        deployment_name="d",
        service_name="s",
        pvc_name="p",
        endpoint_path="/",
        agent_type="workspace",
    )


def test_resolve_github_for_agent_success():
    db = _session()
    user = User(username="u", password_hash="x", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    rp = RuntimeProfile(owner_user_id=user.id, name="rp", config_json=json.dumps({"github": {"enabled": True, "base_url": "https://api.github.com", "api_token": "secret"}}), is_default=True)
    db.add(rp); db.commit(); db.refresh(rp)
    agent = _mk_agent(user.id, rp.id)
    db.add(agent); db.commit(); db.refresh(agent)

    cfg = resolve_github_for_agent(db, agent.id)
    assert cfg.base_url == "https://api.github.com"
    assert cfg.api_token == "secret"
    assert cfg.runtime_profile_id == rp.id


def test_resolve_github_for_agent_failures():
    db = _session()
    user = User(username="u", password_hash="x", role="admin", is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    agent = _mk_agent(user.id, None)
    db.add(agent); db.commit(); db.refresh(agent)

    with pytest.raises(ProviderConfigResolverError, match="does not have a runtime profile"):
        resolve_github_for_agent(db, agent.id)

    rp_disabled = RuntimeProfile(owner_user_id=user.id, name="rp2", config_json=json.dumps({"github": {"enabled": False}}), is_default=False)
    db.add(rp_disabled); db.commit(); db.refresh(rp_disabled)
    agent.runtime_profile_id = rp_disabled.id
    db.add(agent); db.commit()
    with pytest.raises(ProviderConfigResolverError, match="GitHub is not enabled"):
        resolve_github_for_agent(db, agent.id)

    rp_missing = RuntimeProfile(owner_user_id=user.id, name="rp3", config_json=json.dumps({"github": {"enabled": True, "base_url": ""}}), is_default=False)
    db.add(rp_missing); db.commit(); db.refresh(rp_missing)
    agent.runtime_profile_id = rp_missing.id
    db.add(agent); db.commit()
    with pytest.raises(ProviderConfigResolverError, match="base_url/api_token"):
        resolve_github_for_agent(db, agent.id)
