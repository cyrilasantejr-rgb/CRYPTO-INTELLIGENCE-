"""
SQLite-backed position persistence. A single local file, not a database
server - the right-sized tool for this project's actual scale (one
user, a personal paper-trading ledger), not the PostgreSQL mentioned in
the project's original stack. Production alternative, stated plainly
per this project's cost-awareness practice: PostgreSQL, if this ever
needs multi-user/concurrent access - genuinely unnecessary for a single
person's local paper trades.

Deliberately kept separate from position_math.py's pure P&L logic -
this module only knows how to save/load Position objects, never
computes anything itself.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from paper_trading.position_math import Position

DEFAULT_DB_PATH = Path("data") / "paper_trading.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT NOT NULL,
    entry_price REAL NOT NULL,
    entry_timestamp TEXT NOT NULL,
    initial_size REAL NOT NULL,
    remaining_size REAL NOT NULL,
    highest_price_since_entry REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    profit_taking_history TEXT NOT NULL,
    status TEXT NOT NULL
);
"""


class PositionStore:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    def save(self, position: Position, position_id: int | None = None) -> int:
        """
        Inserts a new position if position_id is None, otherwise updates
        the existing row. Returns the row's id either way, so callers
        can capture it from the initial open_position save and pass it
        back in for every subsequent update to the SAME position.
        """
        payload = (
            position.token_address,
            position.entry_price,
            position.entry_timestamp.isoformat(),
            position.initial_size,
            position.remaining_size,
            position.highest_price_since_entry,
            position.realized_pnl,
            json.dumps(position.profit_taking_history),
            position.status,
        )

        with self._connect() as conn:
            if position_id is None:
                cursor = conn.execute(
                    """INSERT INTO positions
                       (token_address, entry_price, entry_timestamp, initial_size,
                        remaining_size, highest_price_since_entry, realized_pnl,
                        profit_taking_history, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    payload,
                )
                return cursor.lastrowid
            else:
                conn.execute(
                    """UPDATE positions SET
                       token_address=?, entry_price=?, entry_timestamp=?,
                       initial_size=?, remaining_size=?,
                       highest_price_since_entry=?, realized_pnl=?,
                       profit_taking_history=?, status=?
                       WHERE id=?""",
                    payload + (position_id,),
                )
                return position_id

    def _row_to_position(self, row: sqlite3.Row) -> Position:
        return Position(
            token_address=row["token_address"],
            entry_price=row["entry_price"],
            entry_timestamp=datetime.fromisoformat(row["entry_timestamp"]),
            initial_size=row["initial_size"],
            remaining_size=row["remaining_size"],
            highest_price_since_entry=row["highest_price_since_entry"],
            realized_pnl=row["realized_pnl"],
            profit_taking_history=json.loads(row["profit_taking_history"]),
            status=row["status"],
        )

    def get(self, position_id: int) -> Position | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM positions WHERE id=?", (position_id,)
            ).fetchone()
        return self._row_to_position(row) if row else None

    def list_open_positions(self) -> list[tuple[int, Position]]:
        """Returns (id, Position) pairs so callers can pass the id back
        into save()/get() for further updates - the Position dataclass
        itself deliberately has no id field, since that's a storage
        concern, not a P&L-math concern."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM positions WHERE status='OPEN'"
            ).fetchall()
        return [(row["id"], self._row_to_position(row)) for row in rows]

    def list_all_positions(self) -> list[tuple[int, Position]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM positions").fetchall()
        return [(row["id"], self._row_to_position(row)) for row in rows]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
