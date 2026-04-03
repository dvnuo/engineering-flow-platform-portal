"""Logging configuration for Portal."""

import logging
import sys

from app.redaction import redact_text, redact_value

# Detailed format with module/function/line info
DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s.%(funcName)s:%(lineno)d | %(message)s"


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts sensitive content in traceback text."""

    def formatException(self, exc_info):
        traceback_text = super().formatException(exc_info)
        return redact_text(traceback_text)


class FormatterRedactionWrapper(logging.Formatter):
    """Wrap an existing formatter while redacting the final rendered output."""

    def __init__(self, inner_formatter: logging.Formatter):
        super().__init__()
        self.inner_formatter = inner_formatter

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(self.inner_formatter.format(record))

    def formatException(self, exc_info):
        format_exception = getattr(self.inner_formatter, "formatException", None)
        if callable(format_exception):
            return redact_text(format_exception(exc_info))
        return redact_text(super().formatException(exc_info))


class RedactingFilter(logging.Filter):
    """Apply lightweight redaction to log records before formatting."""

    @staticmethod
    def _redact_args(args):
        if isinstance(args, dict):
            return redact_value(args)
        if isinstance(args, tuple):
            return tuple(redact_value(value) for value in args)
        return redact_value(args)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if record.args:
                record.args = self._redact_args(record.args)
                if not isinstance(record.msg, str):
                    record.msg = redact_value(record.msg)
            else:
                record.msg = redact_value(record.msg)

            rendered_message = record.getMessage()
            record.msg = redact_value(rendered_message)
            record.args = ()
        except Exception:
            fallback_message = redact_text(str(record.msg))
            if record.args:
                fallback_args = redact_text(str(self._redact_args(record.args)))
                fallback_message = f"{fallback_message} | args={fallback_args}"
            record.msg = fallback_message
            record.args = ()
        return True


def _has_redacting_filter(handler: logging.Handler) -> bool:
    return any(isinstance(log_filter, RedactingFilter) for log_filter in handler.filters)


def _has_redacting_formatter(handler: logging.Handler) -> bool:
    return isinstance(handler.formatter, (RedactingFormatter, FormatterRedactionWrapper))


def setup_logging(level: int = logging.INFO):
    """Setup logging configuration for the application.

    This function should be called explicitly by the application entrypoint
    (for example, in the main CLI or server startup code) to avoid
    configuring logging as a side effect of importing this module.
    """
    root_logger = logging.getLogger()

    created_stdout_handler = None
    if not root_logger.handlers:
        root_logger.setLevel(level)
        created_stdout_handler = logging.StreamHandler(sys.stdout)
        created_stdout_handler.setFormatter(RedactingFormatter(DEFAULT_FORMAT))
        root_logger.addHandler(created_stdout_handler)

    for handler in root_logger.handlers:
        if not _has_redacting_filter(handler):
            handler.addFilter(RedactingFilter())

        if handler is created_stdout_handler:
            continue

        if handler.formatter is None:
            handler.setFormatter(RedactingFormatter(DEFAULT_FORMAT))
        elif not _has_redacting_formatter(handler):
            handler.setFormatter(FormatterRedactionWrapper(handler.formatter))
