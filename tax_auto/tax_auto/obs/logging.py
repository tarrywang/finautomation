"""Structured logging via loguru. See docs/architecture.md §11.1."""

from __future__ import annotations

import sys
from typing import Any

from loguru import logger

from tax_auto.config import get_settings

_CONFIGURED = False


def setup_logging() -> None:
    """Idempotent loguru setup. Call once per process."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    s = get_settings()
    s.logs_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()  # drop default sink

    # Console: human-readable
    logger.add(
        sys.stderr,
        level=s.log_level,
        format=(
            "<green>{time:HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{extra[run_id]:<10}</cyan> | "
            "<cyan>{extra[tax_id]:<18}</cyan> | "
            "{message}"
        ),
    )

    # File: JSON for future aggregation (Loki etc.)
    logger.add(
        s.logs_dir / "tax_auto.log",
        level="DEBUG",
        rotation="50 MB",
        retention="30 days",
        compression="gz",
        serialize=True,
        enqueue=True,  # async-safe
    )

    # Sensible defaults for the extras used in format string
    logger.configure(extra={"run_id": "-", "tax_id": "-"})
    _CONFIGURED = True


def bind(**kwargs: Any) -> Any:
    """Convenience: scoped logger with extras pre-set.

    Example:
        log = bind(run_id="01HX...", tax_id="91310000XX")
        log.info("step started", step="step_2_navigate")
    """
    setup_logging()
    return logger.bind(**kwargs)
