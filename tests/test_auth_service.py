"""Tests for auth_service module."""
import pytest
from app.services.auth_service import (
    issue_session_token,
    parse_session_token,
    hash_password,
    verify_password,
)


def test_create_and_parse_session_token():
    """Test token creation and parsing."""
    user_id = 123
    token = issue_session_token(user_id)
    assert token is not None
    assert isinstance(token, str)
    
    # Parse token
    parsed_user_id = parse_session_token(token)
    assert parsed_user_id == user_id


def test_parse_invalid_token():
    """Test parsing of invalid token."""
    result = parse_session_token("invalid-token")
    assert result is None


def test_password_hashing():
    """Test password hashing."""
    password = "testpassword123"
    hashed = hash_password(password)
    assert hashed is not None
    assert hashed != password


def test_verify_password():
    """Test password verification."""
    password = "testpassword123"
    hashed = hash_password(password)
    
    assert verify_password(password, hashed) is True
    assert verify_password("wrongpassword", hashed) is False
