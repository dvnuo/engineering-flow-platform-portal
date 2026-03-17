"""Tests for logger module."""
import pytest
import logging
from app.logger import setup_logging


def test_setup_logging():
    """Test logging setup."""
    setup_logging()
    # Verify logging is configured
    logger = logging.getLogger("app")
    assert logger is not None
