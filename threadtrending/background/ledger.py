"""SQLite ledger that guarantees a background clip is never reused.

Reserve-then-confirm semantics: a candidate is inserted as ``reserved`` before it
is downloaded, and only promoted to ``confirmed`` once a render succeeds. A run
that crashes releases its reservation, so a failed attempt never permanently
burns a clip -- which is what happens if you record usage optimistically.

``rejected_low_motion`` is sticky on purpose: a calm clip that failed the motion
gate should never be downloaded a second time.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from ..paths import PATHS

log = logging.getLogger(__name__)

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS used_background (
  id             INTEGER PRIMARY KEY,
  provider       TEXT NOT NULL,
  video_id       TEXT NOT NULL,
  file_url       TEXT,
  content_sha256 TEXT,
  duration       REAL, width INTEGER, height INTEGER, fps REAL,
  motion_score   REAL,
  query          TEXT,
  status         TEXT NOT NULL,
  run_id         TEXT,
  used_at        TEXT NOT NULL,
  UNIQUE(provider, video_id)
);
CREATE INDEX IF NOT EXISTS ix_bg_hash   ON used_background(content_sha256);
CREATE INDEX IF NOT EXISTS ix_bg_status ON used_background(status);

CREATE TABLE IF NOT EXISTS run (
  run_id  TEXT PRIMARY KEY,
  url     TEXT, post_code TEXT, trend_name TEXT,
  created_at TEXT, status TEXT, out_path TEXT,
  background_id INTEGER, meta TEXT
);

CREATE TABLE IF NOT EXISTS api_call (
  id INTEGER PRIMARY KEY,
  provider TEXT NOT NULL,
  called_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_api_provider ON api_call(provider, called_at);
"""

BLOCKING_STATUSES = ("confirmed", "reserved", "rejected_low_motion")
RECYCLE_AFTER_DAYS = 180


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    """Thread-safe wrapper around the SQLite file."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or PATHS.ledger
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # --- background clips -------------------------------------------------
    def blocked_ids(self, provider: str) -> set[str]:
        """Video ids this provider must not offer again."""
        placeholders = ",".join("?" * len(BLOCKING_STATUSES))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT video_id FROM used_background "
                f"WHERE provider = ? AND status IN ({placeholders})",
                (provider, *BLOCKING_STATUSES),
            ).fetchall()
        return {row["video_id"] for row in rows}

    def reserve(self, provider: str, video_id: str, *, file_url: str,
                query: str, run_id: str) -> int | None:
        """Claim a clip. Returns None if someone else already holds it."""
        with self._lock, self._connect() as conn:
            try:
                cursor = conn.execute(
                    "INSERT INTO used_background "
                    "(provider, video_id, file_url, query, status, run_id, used_at) "
                    "VALUES (?, ?, ?, ?, 'reserved', ?, ?)",
                    (provider, video_id, file_url, query, run_id, _now()),
                )
                return int(cursor.lastrowid)
            except sqlite3.IntegrityError:
                return None

    def release(self, row_id: int) -> None:
        """Undo a reservation so a crashed run does not burn the clip."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM used_background WHERE id = ? AND status = 'reserved'",
                (row_id,),
            )

    def mark(self, row_id: int, status: str, **fields: object) -> None:
        allowed = ("content_sha256", "duration", "width", "height", "fps", "motion_score")
        sets = ["status = ?"]
        values: list[object] = [status]
        for key in allowed:
            if key in fields:
                sets.append(f"{key} = ?")
                values.append(fields[key])
        values.append(row_id)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE used_background SET {', '.join(sets)} WHERE id = ?", values
            )

    def hash_seen(self, sha256: str) -> bool:
        """Catches the same clip mirrored under different ids on both providers."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM used_background WHERE content_sha256 = ? LIMIT 1",
                (sha256,),
            ).fetchone()
        return row is not None

    def recyclable(self, limit: int = 20) -> list[sqlite3.Row]:
        """Oldest confirmed clips, for when both providers are exhausted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RECYCLE_AFTER_DAYS)).isoformat()
        with self._lock, self._connect() as conn:
            return conn.execute(
                "SELECT * FROM used_background WHERE status = 'confirmed' AND used_at < ? "
                "ORDER BY used_at ASC LIMIT ?",
                (cutoff, limit),
            ).fetchall()

    def confirmed_count(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM used_background WHERE status = 'confirmed'"
            ).fetchone()
        return int(row["n"])

    # --- rate limiting ----------------------------------------------------
    def record_call(self, provider: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO api_call (provider, called_at) VALUES (?, ?)",
                (provider, _now()),
            )

    def calls_in_last_hour(self, provider: str) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM api_call WHERE provider = ? AND called_at >= ?",
                (provider, cutoff),
            ).fetchone()
        return int(row["n"])

    def prune_calls(self, older_than_hours: int = 48) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM api_call WHERE called_at < ?", (cutoff,))

    # --- runs -------------------------------------------------------------
    def start_run(self, run_id: str, *, url: str, post_code: str, trend_name: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO run "
                "(run_id, url, post_code, trend_name, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, 'running')",
                (run_id, url, post_code, trend_name, _now()),
            )

    def finish_run(self, run_id: str, *, status: str, out_path: str = "",
                   background_id: int | None = None) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE run SET status = ?, out_path = ?, background_id = ? WHERE run_id = ?",
                (status, out_path, background_id, run_id),
            )

    def run_count(self) -> int:
        with self._lock, self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM run").fetchone()["n"])
