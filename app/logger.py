"""Logging configuration for Portal."""

import logging
import sys

# Detailed format with module/function/line info
DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s"


def setup_logging(level: int = logging.INFO):
    """Setup logging configuration for the application."""
    logging.basicConfig(
        level=level,
        format=DEFAULT_FORMAT,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ]
    )


# Auto-setup on import (can be called explicitly if needed)
setup_logging()
