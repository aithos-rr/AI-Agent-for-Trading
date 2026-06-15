"""Structlog JSON logging configuration (PRD §11.3, invariant #10)."""

from __future__ import annotations

import logging
from typing import Literal

import structlog

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


def configure_logging(level: LogLevel = "INFO") -> None:
    """Configure structlog with JSON renderer.

    Must be called once at process startup before any log statements.
    Invariant #10: no print() in runtime — structlog replaces all output.

    Args:
        level: Minimum log level (DEBUG/INFO/WARNING/ERROR).
    """
    numeric_level = getattr(logging, level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=numeric_level)
