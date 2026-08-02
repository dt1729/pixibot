"""Lightweight file logging so a run's behaviour is inspectable after the fact.

One append-only log file (default ``~/.pixibot/pixibot.log``). Call :func:`setup`
once at startup; everywhere else grab a child logger with :func:`get`. Before
setup runs there is simply no handler, so library/test use logs nothing and has
no side effects.
"""

from __future__ import annotations

import logging
import os

DEFAULT_PATH = os.path.expanduser("~/.pixibot/pixibot.log")
_CONFIGURED = False


def setup(path: str | None = None, level: int = logging.INFO) -> str:
    """Attach a file handler to the 'pixibot' logger. Idempotent. Returns the path."""
    global _CONFIGURED
    path = path or DEFAULT_PATH
    logger = logging.getLogger("pixibot")
    logger.setLevel(level)
    if not _CONFIGURED:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-5s %(name)s: %(message)s", "%H:%M:%S"))
        logger.addHandler(handler)
        logger.propagate = False  # don't spam the TUI/stdout
        _CONFIGURED = True
        logger.info("──────── pixibot log start (pid %d) ────────", os.getpid())
    return path


def get(name: str = "") -> logging.Logger:
    """A child logger, e.g. get('agent') -> logger 'pixibot.agent'."""
    return logging.getLogger(f"pixibot.{name}" if name else "pixibot")
