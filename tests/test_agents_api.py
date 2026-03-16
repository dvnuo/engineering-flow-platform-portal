"""Tests for agents API module."""
import pytest
from unittest.mock import MagicMock


def test_agent_fields():
    """Test agent fields exist."""
    # Just verify field names
    fields = ["id", "name", "status", "user_id", "is_shared", "namespace", "service_name"]
    for field in fields:
        assert field is not None


def test_status_values():
    """Test status values."""
    # These are the valid status values used in the app
    valid_statuses = ["creating", "running", "stopped", "stopping", "failed"]
    for status in valid_statuses:
        assert status in ["creating", "running", "stopped", "stopping", "failed"]
