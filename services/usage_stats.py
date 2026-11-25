"""App launch counts in SQLite (used to sort the picker and for the Dashboard)."""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path


class UsageStats:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as con, con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS app_usage ("
                "name TEXT PRIMARY KEY, count INTEGER NOT NULL, last_used REAL NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path), timeout=5)

    def record(self, name: str) -> None:
        key = name.strip().lower()
        with closing(self._connect()) as con, con:
            con.execute(
                "INSERT INTO app_usage(name, count, last_used) VALUES(?, 1, ?) "
                "ON CONFLICT(name) DO UPDATE SET count = count + 1, last_used = excluded.last_used",
                (key, time.time()),
            )

    def count(self, name: str) -> int:
        with closing(self._connect()) as con:
            row = con.execute("SELECT count FROM app_usage WHERE name = ?", (name.strip().lower(),)).fetchone()
        return row[0] if row else 0

    def top(self, n: int = 5) -> list[tuple[str, int]]:
        with closing(self._connect()) as con:
            return con.execute(
                "SELECT name, count FROM app_usage ORDER BY count DESC, last_used DESC LIMIT ?", (n,)
            ).fetchall()
