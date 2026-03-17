"""Tests for web routes - access control and helpers."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException


def test_can_access_owner():
    """Test owner can access their agent."""
    from app.web import _can_access
    
    # Mock agent owned by user
    agent = MagicMock()
    agent.owner_user_id = "user-123"
    agent.visibility = "private"
    
    # Mock user
    user = MagicMock()
    user.id = "user-123"
    user.role = "user"
    
    assert _can_access(agent, user) is True


def test_cannot_access_other_user():
    """Test other users cannot access private agent."""
    from app.web import _can_access
    
    agent = MagicMock()
    agent.owner_user_id = "user-123"
    agent.visibility = "private"
    
    user = MagicMock()
    user.id = "user-456"
    user.role = "user"
    
    assert _can_access(agent, user) is False


def test_admin_can_access_all():
    """Test admin can access any agent."""
    from app.web import _can_access
    
    agent = MagicMock()
    agent.owner_user_id = "user-123"
    agent.visibility = "private"
    
    user = MagicMock()
    user.id = "user-456"
    user.role = "admin"
    
    assert _can_access(agent, user) is True


def test_can_access_public_agent():
    """Test can access public agent."""
    from app.web import _can_access
    
    agent = MagicMock()
    agent.owner_user_id = "user-123"
    agent.visibility = "public"
    
    user = MagicMock()
    user.id = "user-456"
    user.role = "user"
    
    assert _can_access(agent, user) is True
