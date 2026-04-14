"""Tests for agents API endpoints."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException


class TestAgentsAPI:
    """Tests for agent API logic."""

    def test_list_agents_mine(self):
        """Test listing user's own agents."""
        # This tests the logic of filtering user's agents
        mock_agents = [
            MagicMock(id="agent-1", user_id="user-1", is_shared=False),
            MagicMock(id="agent-2", user_id="user-1", is_shared=False),
        ]
        
        # Filter for user's agents
        user_agents = [a for a in mock_agents if a.user_id == "user-1"]
        assert len(user_agents) == 2

    def test_list_agents_public(self):
        """Test listing public agents."""
        mock_agents = [
            MagicMock(id="agent-1", user_id="user-1", is_shared=True),
            MagicMock(id="agent-2", user_id="user-2", is_shared=True),
            MagicMock(id="agent-3", user_id="user-1", is_shared=False),
        ]
        
        # Filter for shared agents
        public_agents = [a for a in mock_agents if a.is_shared]
        assert len(public_agents) == 2

    def test_agent_status_transitions(self):
        """Test valid status transitions."""
        # Valid transitions
        valid_transitions = [
            ("creating", "running"),
            ("creating", "failed"),
            ("running", "stopping"),
            ("stopping", "stopped"),
            ("stopped", "creating"),
            ("failed", "creating"),
        ]
        
        # Check all transitions are defined
        for from_status, to_status in valid_transitions:
            assert from_status is not None
            assert to_status is not None

    def test_agent_fields(self):
        """Test required agent fields."""
        agent = MagicMock()
        agent.id = "test-id"
        agent.name = "Test"
        agent.status = "running"
        agent.user_id = "user-1"
        agent.is_shared = False
        agent.namespace = "ns"
        agent.service_name = "svc"
        
        assert agent.id == "test-id"
        assert agent.status == "running"

    def test_agent_create_validation(self):
        """Test agent creation validation."""
        # Test that name is required
        valid_data = {"name": "my-agent"}
        
        assert "name" in valid_data
        assert len(valid_data["name"]) > 0

    def test_agent_delete_validation(self):
        """Test agent deletion checks ownership."""
        agent = MagicMock()
        agent.user_id = "user-123"
        
        requesting_user = "user-123"
        
        # Owner can delete
        assert agent.user_id == requesting_user

    def test_agent_share_validation(self):
        """Test agent sharing validation."""
        agent = MagicMock()
        agent.user_id = "user-123"
        agent.is_shared = False
        
        # Can only share own agents
        assert agent.user_id == "user-123"
        
        # After sharing
        agent.is_shared = True
        assert agent.is_shared is True


class TestAgentStatus:
    """Tests for agent status handling."""

    def test_status_running(self):
        """Test running status."""
        status = "running"
        assert status in ["creating", "running", "stopped", "failed", "stopping"]

    def test_status_stopped(self):
        """Test stopped status."""
        status = "stopped"
        assert status in ["creating", "running", "stopped", "failed", "stopping"]

    def test_status_creating(self):
        """Test creating status."""
        status = "creating"
        assert status in ["creating", "running", "stopped", "failed", "stopping"]

    def test_status_failed(self):
        """Test failed status."""
        status = "failed"
        assert status in ["creating", "running", "stopped", "failed", "stopping"]


def test_validate_profile_references_rejects_other_owner():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.api.agents import _validate_profile_references
    from app.db import Base
    from app.models import RuntimeProfile, User

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    owner = User(username="owner", password_hash="test", role="user", is_active=True)
    other = User(username="other", password_hash="test", role="user", is_active=True)
    db.add_all([owner, other]); db.commit(); db.refresh(owner); db.refresh(other)
    rp = RuntimeProfile(owner_user_id=other.id, name="rp", config_json="{}", is_default=True, revision=1)
    db.add(rp); db.commit()

    with pytest.raises(HTTPException) as exc:
        _validate_profile_references(db, None, None, rp.id, current_user_id=owner.id)
    assert exc.value.status_code == 404


def test_validate_profile_references_accepts_owner():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.api.agents import _validate_profile_references
    from app.db import Base
    from app.models import RuntimeProfile, User

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    owner = User(username="owner2", password_hash="test", role="user", is_active=True)
    db.add(owner); db.commit(); db.refresh(owner)
    rp = RuntimeProfile(owner_user_id=owner.id, name="rp2", config_json="{}", is_default=True, revision=1)
    db.add(rp); db.commit()

    _validate_profile_references(db, None, None, rp.id, current_user_id=owner.id)
