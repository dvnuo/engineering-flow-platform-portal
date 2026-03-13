"""Logging configuration for Portal."""

import logging
import sys
from typing import Optional

# Detailed format with module/function/line info
DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s"

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format=DEFAULT_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name."""
    return logging.getLogger(name)
