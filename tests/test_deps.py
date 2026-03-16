"""Tests for dependencies/deps."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException


def test_require_admin_not_admin():
    """Test require_admin when user is not admin."""
    from app.deps import require_admin
    
    # Create a mock user that's not an admin
    user = MagicMock()
    user.role = "user"  # Not admin
    
    with pytest.raises(HTTPException) as exc:
        require_admin(user)
    
    assert exc.value.status_code == 403


def test_require_admin_is_admin():
    """Test require_admin when user is admin."""
    from app.deps import require_admin
    
    # Create a mock admin user
    user = MagicMock()
    user.role = "admin"
    
    # Should not raise
    result = require_admin(user)
    assert result == user
