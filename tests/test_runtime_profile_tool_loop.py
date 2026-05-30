import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import RuntimeProfile, User
from app.services.auth_service import hash_password
from app.schemas.runtime_profile import sanitize_runtime_profile_config_dict
from app.services.runtime_execution_context_service import RuntimeExecutionContextService
from app.services.runtime_profile_service import RuntimeProfileService


def test_runtime_profile_tool_loop_is_dropped():
    raw = {
        "llm": {
            "tool_loop": {
                "one_tool_per_turn": True,
                "parallel_tool_calls": False,
                "max_repeated_tool_signature": 2,
            }
        }
    }

    parsed = sanitize_runtime_profile_config_dict(raw)

    assert parsed == {}


def test_default_runtime_profile_does_not_contain_tool_loop_defaults():
    cfg = RuntimeProfileService.default_profile_config()

    assert "tool_loop" not in cfg["llm"]
    assert "tools" not in cfg["llm"]
    assert "response_flow" not in cfg["llm"]


def test_runtime_metadata_includes_runtime_profile_and_tool_loop(monkeypatch):
    service = RuntimeExecutionContextService()

    monkeypatch.setattr(
        service,
        "build_for_agent",
        lambda _db, agent: {
            "runtime_profile_id": agent.runtime_profile_id,
            "runtime_profile_context": {
                "runtime_profile_id": "rp-1",
                "revision": 7,
                "config": {
                    "llm": {
                        "provider": "github_copilot",
                        "model": "gpt-5.4-mini",
                    }
                },
            },
        },
    )

    agent = SimpleNamespace(runtime_profile_id="rp-1", runtime_type="native")
    metadata = service.build_runtime_metadata(db=object(), agent=agent)

    assert metadata["runtime_profile_id"] == "rp-1"
    assert metadata["runtime_profile"]["runtime_profile_id"] == "rp-1"
    assert metadata["runtime_profile"]["revision"] == 7
    assert metadata["runtime_profile"]["provider"] == "github_copilot"
    assert metadata["runtime_profile"]["model"] == "gpt-5.4-mini"
    assert metadata["runtime_profile"]["config"]["llm"]["provider"] == "github_copilot"
    assert metadata["runtime_profile"]["config"]["llm"]["model"] == "gpt-5.4-mini"
    assert metadata["model"] == "gpt-5.4-mini"
    assert "tool_loop" not in metadata["runtime_profile"]["config"]["llm"]
    assert "llm_tool_loop" not in metadata


def test_runtime_metadata_does_not_materialize_default_tool_loop_for_sparse_profile(monkeypatch):
    service = RuntimeExecutionContextService()
    profile = SimpleNamespace(id="rp-1", config_json='{"llm": {"provider": "github_copilot"}}')

    import app.services.runtime_execution_context_service as module

    monkeypatch.setattr(
        module,
        "RuntimeProfileRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _profile_id: profile),
    )

    runtime_profile_id, runtime_context = service._build_runtime_profile_context(
        db=object(),
        agent=SimpleNamespace(
            id="agent-1",
            runtime_profile_id="rp-1",
        ),
    )

    assert runtime_profile_id == "rp-1"
    assert runtime_context["runtime_profile_id"] == "rp-1"
    assert runtime_context["revision"] is None
    assert runtime_context["config"]["llm"]["provider"] == "github_copilot"
    assert runtime_context["config"]["llm"]["model"] == "gpt-5.4-mini"
    assert "tool_loop" not in runtime_context["config"]["llm"]


def test_runtime_metadata_drops_explicit_tool_loop_override(monkeypatch):
    service = RuntimeExecutionContextService()
    profile = SimpleNamespace(
        id="rp-1",
        config_json='{"llm": {"tool_loop": {"one_tool_per_turn": false, "parallel_tool_calls": true, "max_repeated_tool_signature": 3}}}',
    )

    import app.services.runtime_execution_context_service as module

    monkeypatch.setattr(
        module,
        "RuntimeProfileRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _profile_id: profile),
    )

    runtime_profile_id, runtime_context = service._build_runtime_profile_context(
        db=object(),
        agent=SimpleNamespace(
            id="agent-1",
            runtime_profile_id="rp-1",
        ),
    )

    assert runtime_profile_id == "rp-1"
    assert runtime_context["config"]["llm"]["provider"] == "github_copilot"
    assert runtime_context["config"]["llm"]["model"] == "gpt-5.4-mini"
    assert "tool_loop" not in runtime_context["config"]["llm"]


@pytest.fixture()
def runtime_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    user = User(username="rp-owner", password_hash=hash_password("pw"), role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield db, user
    db.close()


def test_runtime_metadata_does_not_infer_github_authorization_from_credentials(runtime_db):
    db, user = runtime_db
    profile = RuntimeProfile(
        owner_user_id=user.id,
        name="github-auth",
        config_json=json.dumps(
            {
                "github": {
                    "enabled": True,
                    "base_url": "https://api.github.com",
                    "api_token": "ghp_SECRET",
                }
            }
        ),
        revision=4,
        is_default=True,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)

    service = RuntimeExecutionContextService()
    agent = SimpleNamespace(id="agent-1", runtime_profile_id=profile.id, runtime_type="native")
    metadata = service.build_runtime_metadata(db, agent)

    assert metadata["runtime_profile_id"] == profile.id
    assert metadata["runtime_profile"]["runtime_profile_id"] == profile.id
    assert "authorization_source" not in metadata
    assert "allowed_external_systems" not in metadata
    assert "allowed_actions" not in metadata
    assert "allowed_adapter_actions" not in metadata
    assert "allowed_capability_ids" not in metadata
    assert "allowed_capability_types" not in metadata
    assert "resolved_action_mappings" not in metadata
    assert metadata["runtime_profile"]["config"]["github"]["api_token"] == "ghp_SECRET"
