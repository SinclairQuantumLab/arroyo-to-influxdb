"""Small stdout/stderr logging helpers suitable for Supervisor capture."""

from __future__ import annotations

import sys
from datetime import UTC, datetime


def _prefix() -> str:
    """Return the UTC timestamp prefix used for every Supervisor log line."""

    return datetime.now(UTC).isoformat(timespec="seconds")


def log(message: str, *, end: str | None = "\n") -> None:
    """Write an ordinary timestamped status message to stdout."""

    print(f"{_prefix()} {message}", end=end, flush=True)


def log_warn(message: str) -> None:
    """Write a timestamped recoverable warning to stderr."""

    print(f"{_prefix()} WARNING {message}", file=sys.stderr, flush=True)


def log_error(message: str) -> None:
    """Write a timestamped error message to stderr."""

    print(f"{_prefix()} ERROR {message}", file=sys.stderr, flush=True)
