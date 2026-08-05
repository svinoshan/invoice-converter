"""Application-wide logging configuration."""

from __future__ import annotations

import logging
import sys

_LOGGER_NAME = "invoice_converter"
_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure and return the application logger (idempotent)."""
    global _CONFIGURED

    logger = logging.getLogger(_LOGGER_NAME)
    if _CONFIGURED:
        return logger

    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    logger.addHandler(handler)
    logger.propagate = False
    _CONFIGURED = True
    return logger


def get_logger(name: str = _LOGGER_NAME) -> logging.Logger:
    """Return a child logger for a module, e.g. get_logger('parser')."""
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")
