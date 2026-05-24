from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import User
from app.services.auth_service import hash_password
from app.schemas.runtime_profile import sanitize_runtime_profile_config_dict
from app.services.runtime_execution_context_service import RuntimeExecutionContextService
from app.services.runtime_profile_service import RuntimeProfileService


def test_runtime_profile_tool_loop_is_preserved():
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

    assert parsed["llm"]["tool_loop"] == {
        "one_tool_per_turn": True,
        "parallel_tool_calls": False,
        "max_repeated_tool_signature": 2,
    }


@pytest.mark.parametrize(
    "payload,match",
    [
        ({"one_tool_per_turn": "true"}, "one_tool_per_turn must be a boolean"),
        ({"parallel_tool_calls": "false"}, "parallel_tool_calls must be a boolean"),
        ({"max_repeated_tool_signature": 0}, "must be between 1 and 10"),
        ({"max_repeated_tool_signature": 11}, "must be between 1 and 10"),
    ],
)
def test_runtime_profile_tool_loop_invalid_values_raise(payload, match):
    with pytest.raises(ValueError, match=match):
        sanitize_runtime_profile_config_dict({"llm": {"tool_loop": payload}})


@pytest.mark.parametrize("bad_value", [True, False])
def test_runtime_profile_tool_loop_rejects_bool_max_repeated_tool_signature(bad_value):
    with pytest.raises(ValueError, match="must be an integer"):
        sanitize_runtime_profile_config_dict(
            {
                "llm": {
                    "tool_loop": {
                        "max_repeated_tool_signature": bad_value,
                    }
                }
            }
        )


def test_default_runtime_profile_contains_tool_loop_defaults():
    cfg = RuntimeProfileService.default_profile_config()

    assert cfg["llm"]["tool_loop"]["one_tool_per_turn"] is True
    assert cfg["llm"]["tool_loop"]["parallel_tool_calls"] is False
    assert cfg["llm"]["tool_loop"]["max_repeated_tool_signature"] == 2


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
                        "tool_loop": {
                            "one_tool_per_turn": True,
                            "parallel_tool_calls": False,
                            "max_repeated_tool_signature": 2,
                        },
                    }
                },
            },
        },
    )

    agent = SimpleNamespace(runtime_profile_id="rp-1", runtime_type="opencode")
    metadata = service.build_runtime_metadata(db=object(), agent=agent)

    assert metadata["runtime_profile_id"] == "rp-1"
    assert metadata["runtime_profile"]["runtime_profile_id"] == "rp-1"
    assert metadata["runtime_profile"]["revision"] == 7
    assert metadata["runtime_profile"]["provider"] == "github-copilot"
    assert metadata["runtime_profile"]["model"] == "github-copilot/gpt-5.4-mini"
    assert metadata["runtime_profile"]["config"]["llm"]["provider"] == "github_copilot"
    assert metadata["runtime_profile"]["config"]["llm"]["model"] == "gpt-5.4-mini"
    assert metadata["model"] == "github-copilot/gpt-5.4-mini"
    assert metadata["runtime_profile"]["config"]["llm"]["tool_loop"]["one_tool_per_turn"] is True
    assert metadata["llm_tool_loop"]["one_tool_per_turn"] is True
    assert metadata["llm_tool_loop"]["parallel_tool_calls"] is False


def test_runtime_metadata_materializes_default_tool_loop_for_sparse_profile(monkeypatch):
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
    assert runtime_context["config"]["llm"]["tool_loop"] == {
        "one_tool_per_turn": True,
        "parallel_tool_calls": False,
        "max_repeated_tool_signature": 2,
    }


def test_runtime_metadata_uses_explicit_tool_loop_override(monkeypatch):
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
    assert runtime_context["config"]["llm"]["tool_loop"] == {
        "one_tool_per_turn": False,
        "parallel_tool_calls": True,
        "max_repeated_tool_signature": 3,
    }


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
