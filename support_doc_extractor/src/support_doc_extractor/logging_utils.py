from __future__ import annotations

import logging
import os
import sys
from typing import TextIO


LOGGER_NAME = "support_doc_extractor"
DEFAULT_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_CONFIGURED = False


def configure_logging(
    level: str | int | None = None,
    *,
    stream: TextIO | None = None,
    force: bool = False,
) -> logging.Logger:
    """Configure console logging for the extractor package."""
    global _CONFIGURED

    logger = logging.getLogger(LOGGER_NAME)
    if _CONFIGURED and not force:
        return logger

    resolved_level = level if level is not None else os.getenv("SUPPORT_DOC_LOG_LEVEL", "INFO")
    if isinstance(resolved_level, str):
        numeric_level = getattr(logging, resolved_level.upper(), logging.INFO)
    else:
        numeric_level = int(resolved_level)

    logger.setLevel(numeric_level)
    logger.propagate = False

    if force:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)

    if not logger.handlers:
        handler = logging.StreamHandler(stream or sys.stdout)
        handler.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT))
        logger.addHandler(handler)

    for handler in logger.handlers:
        handler.setLevel(numeric_level)

    _CONFIGURED = True
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a package logger and configure console output on first use."""
    configure_logging()
    if not name:
        return logging.getLogger(LOGGER_NAME)
    if name.startswith(LOGGER_NAME):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
