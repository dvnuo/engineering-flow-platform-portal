"""Tests for logger module."""

import io
import logging
import sys

from app.logger import DEFAULT_FORMAT, RedactingFilter, RedactingFormatter, setup_logging
from app.log_context import bind_log_context, reset_log_context


def test_setup_logging():
    """Test logging setup."""
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    try:
        setup_logging()
        logger = logging.getLogger("app")
        assert logger is not None
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)


def test_setup_logging_is_idempotent_for_stdout_handler():
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    try:
        root.handlers = []
        setup_logging()
        setup_logging()

        stdout_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stdout
        ]
        assert len(stdout_handlers) == 1
        assert any(isinstance(f, RedactingFilter) for f in stdout_handlers[0].filters)
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)


def test_setup_logging_preserves_preconfigured_handlers_and_format_style():
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level

    stdout_stream = io.StringIO()
    other_stream = io.StringIO()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(logging.Formatter("CUSTOM:%(message)s"))
    test_handler = logging.StreamHandler(other_stream)
    test_handler.setFormatter(logging.Formatter("OTHER:%(message)s"))

    emit_stdout = logging.StreamHandler.emit

    def emit_to_buffer(self, record):
        if self is stdout_handler:
            msg = self.format(record)
            stdout_stream.write(msg + self.terminator)
        else:
            emit_stdout(self, record)

    try:
        root.handlers = [stdout_handler, test_handler]
        root.setLevel(logging.INFO)
        logging.StreamHandler.emit = emit_to_buffer

        setup_logging()
        setup_logging()

        current_stdout_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stdout
        ]
        assert len(current_stdout_handlers) == 1

        for handler in root.handlers:
            assert any(isinstance(f, RedactingFilter) for f in handler.filters)
            assert handler.formatter is not None

        root.info("token=%s", "secret-token")

        stdout_output = stdout_stream.getvalue()
        other_output = other_stream.getvalue()

        assert stdout_output.startswith("CUSTOM:")
        assert other_output.startswith("OTHER:")
        assert "secret-token" not in stdout_output
        assert "secret-token" not in other_output
        assert "[REDACTED]" in stdout_output
        assert "[REDACTED]" in other_output
    finally:
        logging.StreamHandler.emit = emit_stdout
        root.handlers = original_handlers
        root.setLevel(original_level)


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


def test_redaction_filter_redacts_structured_log_args():
    logger = logging.getLogger("tests.redaction.structured")
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.INFO)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)

    payload = {
        "user": "alice",
        "password": "secret",
        "nested": {"token": "abc123"},
        "items": [{"api_key": "key-123"}, {"note": "safe"}],
    }

    logger.info("structured payload=%s", payload)

    output = stream.getvalue()
    assert "secret" not in output
    assert "abc123" not in output
    assert "key-123" not in output
    assert "[REDACTED]" in output


def test_redaction_filter_redacts_exception_traceback():
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("TRACE:%(message)s"))

    logger = logging.getLogger("traceback.redaction.exception")
    logger.handlers = []
    logger.propagate = True
    logger.setLevel(logging.ERROR)

    try:
        root.handlers = [handler]
        setup_logging()

        try:
            raise ValueError("password=secret access_token=abc123")
        except Exception:
            logger.exception("operation failed")

        output = stream.getvalue()
        assert output.startswith("TRACE:")
        assert "secret" not in output
        assert "abc123" not in output
        assert "[REDACTED]" in output
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)


def test_redaction_filter_fallback_sanitizes_and_clears_args():
    logger = logging.getLogger("fallback.redaction")
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.INFO)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)

    logger.info("token=%(token)s", ({"password": "secret"},))

    output = stream.getvalue()
    assert "secret" not in output
    assert "[REDACTED]" in output
    assert "args=" in output


def test_logger_injects_trace_fields_from_context():
    logger = logging.getLogger("tests.trace.context")
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.INFO)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(DEFAULT_FORMAT))
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)

    token = bind_log_context(
        trace_id="trace-1",
        span_id="span-1",
        parent_span_id="parent-1",
        portal_dispatch_id="dispatch-1",
        portal_task_id="task-1",
        agent_id="agent-1",
        path="/api/test",
    )
    try:
        logger.info("hello")
    finally:
        reset_log_context(token)

    output = stream.getvalue()
    assert "trace=trace-1" in output
    assert "span=span-1" in output
    assert "dispatch=dispatch-1" in output
    assert "task=task-1" in output
    assert "agent=agent-1" in output
    assert "path=/api/test" in output


def test_logger_defaults_trace_fields_to_dash_when_unbound():
    logger = logging.getLogger("tests.trace.unbound")
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.INFO)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(DEFAULT_FORMAT))
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)

    logger.info("hello")
    output = stream.getvalue()
    assert "trace=-" in output
    assert "span=-" in output
    assert "parent=-" in output
    assert "dispatch=-" in output
