"""On-disk state: API response cache, quota counters and resolution sessions.

Everything lives under `$XDG_CACHE_HOME/digikey-mcp` (default
`~/.cache/digikey-mcp`): `cache.sqlite3` holds the cache and quota tables,
`sessions/<id>.json` one file per resolution session.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .models import ResolveError

_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MINUTE = 60.0


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME", "").strip()
    return (Path(base) if base else Path.home() / ".cache") / "digikey-mcp"


def utc_day(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def seconds_to_utc_midnight(now: float) -> float:
    return 86400.0 - (now % 86400.0)


class QuotaExhausted(Exception):
    """DigiKey call budget used up. `kind` is minute, day or suspended."""

    def __init__(self, kind: str, retry_after: float, message: str):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


class Store:
    def __init__(self, directory: Path | str | None = None):
        self.dir = Path(directory) if directory else cache_dir()
        self.sessions_dir = self.dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.dir / "cache.sqlite3"), timeout=30, check_same_thread=False)
        with self._lock, self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, stored REAL NOT NULL,
                                                  expires REAL NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS calls (ts REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS daily (day TEXT PRIMARY KEY, count INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value REAL NOT NULL);
                """
            )

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- response cache -------------------------------------------------

    def cache_get(self, key: str, now: float) -> Any | None:
        with self._lock:
            row = self._db.execute("SELECT expires, body FROM cache WHERE key=?", (key,)).fetchone()
            if row is None:
                return None
            if row[0] <= now:
                with self._db:
                    self._db.execute("DELETE FROM cache WHERE key=?", (key,))
                return None
            return json.loads(row[1])

    def cache_put(self, key: str, value: Any, ttl: float, now: float) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO cache (key, stored, expires, body) VALUES (?,?,?,?)",
                (key, now, now + ttl, json.dumps(value)),
            )

    def cache_purge(self, now: float) -> int:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM cache WHERE expires<=?", (now,)).rowcount

    # ---- quota ------------------------------------------------------------

    def reserve_call(self, now: float, per_minute: int, per_day: int) -> float:
        """Record one call if the budget allows and return 0; else return seconds to wait.

        Raises QuotaExhausted for the daily limit or an active suspension.
        """
        with self._lock, self._db:
            blocked = self._db.execute("SELECT value FROM meta WHERE key='blocked_until'").fetchone()
            if blocked and blocked[0] > now:
                raise QuotaExhausted("suspended", blocked[0] - now, "DigiKey suspended the API key; wait before resuming")
            day = utc_day(now)
            row = self._db.execute("SELECT count FROM daily WHERE day=?", (day,)).fetchone()
            if (row[0] if row else 0) >= per_day:
                raise QuotaExhausted("day", seconds_to_utc_midnight(now), f"daily limit of {per_day} calls reached")
            self._db.execute("DELETE FROM calls WHERE ts<=?", (now - MINUTE,))
            stamps = [r[0] for r in self._db.execute("SELECT ts FROM calls ORDER BY ts")]
            if len(stamps) >= per_minute:
                return max(stamps[len(stamps) - per_minute] + MINUTE - now, 0.01)
            self._db.execute("INSERT INTO calls (ts) VALUES (?)", (now,))
            self._db.execute(
                "INSERT INTO daily (day, count) VALUES (?, 1) ON CONFLICT(day) DO UPDATE SET count=count+1", (day,)
            )
            return 0.0

    def block_until(self, until: float) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('blocked_until', ?)", (until,))

    def quota_snapshot(self, now: float, per_minute: int, per_day: int) -> dict[str, Any]:
        with self._lock:
            used_min = self._db.execute("SELECT COUNT(*) FROM calls WHERE ts>?", (now - MINUTE,)).fetchone()[0]
            row = self._db.execute("SELECT count FROM daily WHERE day=?", (utc_day(now),)).fetchone()
            blocked = self._db.execute("SELECT value FROM meta WHERE key='blocked_until'").fetchone()
            oldest = self._db.execute("SELECT MIN(ts) FROM calls WHERE ts>?", (now - MINUTE,)).fetchone()[0]
        used_day = row[0] if row else 0
        return {
            "per_minute_limit": per_minute,
            "used_last_minute": used_min,
            "remaining_this_minute": max(per_minute - used_min, 0),
            "minute_window_frees_in_seconds": round(oldest + MINUTE - now, 1) if oldest else 0.0,
            "per_day_limit": per_day,
            "used_today_utc": used_day,
            "remaining_today": max(per_day - used_day, 0),
            "day_resets_in_seconds": round(seconds_to_utc_midnight(now)),
            "suspended_for_seconds": round(blocked[0] - now) if blocked and blocked[0] > now else 0,
        }

    # ---- sessions ---------------------------------------------------------

    @staticmethod
    def check_session_id(session_id: str) -> str:
        if not isinstance(session_id, str) or not _SESSION_ID.match(session_id):
            raise ResolveError("session_id must be 1-64 characters: letters, digits, '_', '-', '.'")
        return session_id

    def session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{self.check_session_id(session_id)}.json"

    def session_exists(self, session_id: str) -> bool:
        return self.session_path(session_id).is_file()

    def session_load(self, session_id: str) -> dict[str, Any]:
        path = self.session_path(session_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ResolveError(f"unknown session {session_id!r}") from None
        except ValueError:
            raise ResolveError(f"session {session_id!r} is corrupt: {path}") from None

    def session_save(self, session_id: str, data: dict[str, Any]) -> None:
        path = self.session_path(session_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)


@contextmanager
def open_store(directory: Path | str | None = None) -> Iterator[Store]:
    store = Store(directory)
    try:
        yield store
    finally:
        store.close()
