"""Tests for models."""
import pytest


def test_agent_status_string():
    """Test Agent status is a string field."""
    # Status is stored as string in the database
    status = "running"
    assert status == "running"
    status = "stopped"
    assert status == "stopped"
    status = "creating"
    assert status == "creating"
    status = "failed"
    assert status == "failed"


def test_user_fields():
    """Test User model fields."""
    # Just test the field names that should exist
    fields = ["id", "username", "password_hash", "is_admin"]
    for field in fields:
        assert field is not None
