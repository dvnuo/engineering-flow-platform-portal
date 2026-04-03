"""Logging configuration for Portal."""

import logging
import sys

from app.redaction import redact_value

# Detailed format with module/function/line info
DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s.%(funcName)s:%(lineno)d | %(message)s"


class RedactingFilter(logging.Filter):
    """Apply lightweight redaction to log records before formatting."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered_message = record.getMessage()
            record.msg = redact_value(rendered_message)
            record.args = ()
        except Exception:
            record.msg = redact_value(record.msg)
            if record.args:
                record.args = redact_value(record.args)
        return True


def _has_redacting_filter(handler: logging.Handler) -> bool:
    return any(isinstance(log_filter, RedactingFilter) for log_filter in handler.filters)


def setup_logging(level: int = logging.INFO):
    """Setup logging configuration for the application.

    This function should be called explicitly by the application entrypoint
    (for example, in the main CLI or server startup code) to avoid
    configuring logging as a side effect of importing this module.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    existing_handler = None
    for handler in root_logger.handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stdout:
            existing_handler = handler
            break

    if existing_handler is None:
        existing_handler = logging.StreamHandler(sys.stdout)
        root_logger.addHandler(existing_handler)

    existing_handler.setFormatter(logging.Formatter(DEFAULT_FORMAT))
    if not _has_redacting_filter(existing_handler):
        existing_handler.addFilter(RedactingFilter())
