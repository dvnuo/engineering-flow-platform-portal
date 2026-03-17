"""Tests for models."""


def test_agent_status_string():
    """Test Agent status field properties."""
    from app.models.agent import Agent
    from sqlalchemy import inspect
    
    mapper = inspect(Agent)
    status_col = mapper.columns.get("status")
    assert status_col is not None, "Agent should have status column"
    assert status_col.type.__class__.__name__ == "String"
    assert status_col.default.arg == "creating", "Default status should be creating"


def test_user_fields():
    """Test User model fields."""
    from app.models.user import User
    from sqlalchemy import inspect
    
    mapper = inspect(User)
    columns = [c.name for c in mapper.columns]
    assert "id" in columns
    assert "username" in columns
    assert "role" in columns
    assert "nickname" in columns
