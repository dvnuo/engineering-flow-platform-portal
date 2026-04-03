"""Tests for logger module."""

import io
import logging

from app.logger import RedactingFilter, setup_logging


def test_setup_logging():
    """Test logging setup."""
    setup_logging()
    logger = logging.getLogger("app")
    assert logger is not None


def test_setup_logging_is_idempotent_for_stdout_handler():
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    try:
        root.handlers = []
        setup_logging()
        setup_logging()

        stdout_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is not None
        ]
        assert len(stdout_handlers) == 1
        assert any(isinstance(f, RedactingFilter) for f in stdout_handlers[0].filters)
    finally:
        root.handlers = original_handlers


def test_redaction_filter_redacts_log_args():
    logger = logging.getLogger("tests.redaction")
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.INFO)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)

    logger.info("credentials token=%s password=%s", "abc123", "secret")

    output = stream.getvalue()
    assert "abc123" not in output
    assert "secret" not in output
    assert "[REDACTED]" in output
