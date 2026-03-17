"""Tests for db module."""
import pytest
from app.db import get_db, Base


def test_get_db():
    """Test get_db function."""
    # get_db should be callable
    assert callable(get_db)


def test_base_model():
    """Test Base model exists."""
    assert Base is not None
