"""Tests for agents API module."""
from app.models.agent import Agent
from app.schemas.agent import AgentResponse
from sqlalchemy import inspect


def test_agent_model_fields():
    """Test Agent model has expected fields."""
    mapper = inspect(Agent)
    columns = [c.name for c in mapper.columns]
    
    # Check key fields exist
    assert "id" in columns
    assert "name" in columns
    assert "status" in columns
    assert "visibility" in columns
    assert "owner_user_id" in columns
    assert "agent_type" in columns
    assert "capability_profile_id" in columns
    assert "policy_profile_id" in columns


def test_agent_response_schema():
    """Test AgentResponse schema fields."""
    fields = AgentResponse.model_fields.keys()
    
    # Check key fields in response
    assert "id" in fields
    assert "name" in fields
    assert "status" in fields
    assert "visibility" in fields
    assert "agent_type" in fields
    assert "capability_profile_id" in fields
    assert "policy_profile_id" in fields


def test_agent_status_values():
    """Test valid Agent status values from state machine."""
    from app.utils.state_machine import VALID_STATUSES
    
    # Check that valid statuses are defined
    assert "running" in VALID_STATUSES
    assert "stopped" in VALID_STATUSES
    assert "creating" in VALID_STATUSES
