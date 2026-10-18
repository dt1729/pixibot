"""Lightweight file logging so a run's behaviour is inspectable after the fact.

One append-only log file (default ``~/.pixibot/pixibot.log``). Call :func:`setup`
once at startup; everywhere else grab a child logger with :func:`get`. Before
setup runs there is simply no handler, so library/test use logs nothing and has
no side effects.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

DEFAULT_PATH = str(Path("~/.pixibot/pixibot.log").expanduser())

_HANDLER_TAG = "pixibot-run-log"  # marks our handler, so setup stays idempotent
                                  # without module-level mutable state


def setup(path: str | None = None, level: int = logging.INFO) -> str:
    """Attach a file handler to the 'pixibot' logger. Idempotent. Returns the path."""
    target = path or DEFAULT_PATH
    logger = logging.getLogger("pixibot")
    logger.setLevel(level)
    if any(getattr(h, "name", None) == _HANDLER_TAG for h in logger.handlers):
        return target
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(target, encoding="utf-8")
    handler.name = _HANDLER_TAG
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-5s %(name)s: %(message)s", "%H:%M:%S"))
    logger.addHandler(handler)
    logger.propagate = False  # don't spam the TUI/stdout
    logger.info("──────── pixibot log start (pid %d) ────────", os.getpid())
    return target


def get(name: str = "") -> logging.Logger:
    """A child logger, e.g. get('agent') -> logger 'pixibot.agent'."""
    return logging.getLogger(f"pixibot.{name}" if name else "pixibot")
