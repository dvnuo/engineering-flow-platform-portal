"""Logging configuration for Portal."""

import logging
import sys

# Detailed format with module/function/line info
DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s.%(funcName)s:%(lineno)d | %(message)s"


def setup_logging(level: int = logging.INFO):
    """Setup logging configuration for the application.

    This function should be called explicitly by the application entrypoint
    (for example, in the main CLI or server startup code) to avoid
    configuring logging as a side effect of importing this module.
    """
    logging.basicConfig(
        level=level,
        format=DEFAULT_FORMAT,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ]
    )

