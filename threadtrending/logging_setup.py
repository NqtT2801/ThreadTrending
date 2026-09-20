"""Console + rotating file logging, with the run id on every record."""
from __future__ import annotations

import logging
import logging.handlers
import sys
from contextvars import ContextVar

from .paths import PATHS

_run_id: ContextVar[str] = ContextVar("run_id", default="-")

FORMAT = "%(asctime)s %(levelname)-7s [%(run_id)s] %(name)s: %(message)s"


class _RunIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id.get()
        return True


def set_run_id(run_id: str) -> None:
    _run_id.set(run_id)


def setup(level: int = logging.INFO) -> None:
    # Windows consoles default to cp1252; Vietnamese text would raise on every
    # log line. Reconfigure before any handler is attached.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass

    root = logging.getLogger()
    if any(getattr(h, "_tt_configured", False) for h in root.handlers):
        return

    formatter = logging.Formatter(FORMAT, datefmt="%H:%M:%S")
    run_filter = _RunIdFilter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(run_filter)
    console._tt_configured = True  # type: ignore[attr-defined]

    file_handler = logging.handlers.RotatingFileHandler(
        PATHS.logs / "threadtrending.log",
        maxBytes=2_000_000, backupCount=3, encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(run_filter)
    file_handler._tt_configured = True  # type: ignore[attr-defined]

    root.setLevel(level)
    root.addHandler(console)
    root.addHandler(file_handler)

    # Playwright and httpx are extremely chatty at DEBUG.
    for noisy in ("httpx", "httpcore", "urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
