from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path("data/autotrade.db")


class Database:
    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def _init_db(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT,
                    symbol TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    pnl REAL DEFAULT 0,
                    pnl_pct REAL DEFAULT 0,
                    fees REAL DEFAULT 0,
                    reason_enter TEXT,
                    reason_exit TEXT,
                    status TEXT NOT NULL DEFAULT 'OPEN'
                );
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    total_score REAL NOT NULL,
                    stage_scores TEXT NOT NULL,
                    pass_fail TEXT NOT NULL,
                    reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS engine_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def insert_signal(self, symbol: str, total_score: float, stage_scores: str, pass_fail: str, reason: str) -> None:
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO signals (created_at, symbol, total_score, stage_scores, pass_fail, reason)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (datetime.utcnow().isoformat(), symbol, total_score, stage_scores, pass_fail, reason),
            )

    def open_trade(self, symbol: str, qty: int, entry_price: float, reason_enter: str) -> int:
        with self.connect() as con:
            cur = con.execute(
                """
                INSERT INTO trades (entry_time, symbol, qty, entry_price, reason_enter, status)
                VALUES (?, ?, ?, ?, ?, 'OPEN')
                """,
                (datetime.utcnow().isoformat(), symbol, qty, entry_price, reason_enter),
            )
            return int(cur.lastrowid)

    def close_trade(self, trade_id: int, exit_price: float, fees: float, reason_exit: str) -> None:
        with self.connect() as con:
            row = con.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
            if not row:
                return
            pnl = (exit_price - row["entry_price"]) * row["qty"] - fees
            pnl_pct = (exit_price / row["entry_price"] - 1) * 100
            con.execute(
                """
                UPDATE trades
                SET exit_time=?, exit_price=?, pnl=?, pnl_pct=?, fees=?, reason_exit=?, status='CLOSED'
                WHERE id=?
                """,
                (datetime.utcnow().isoformat(), exit_price, pnl, pnl_pct, fees, reason_exit, trade_id),
            )

    def set_engine_state(self, key: str, value: str) -> None:
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO engine_state(key, value, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, datetime.utcnow().isoformat()),
            )

    def get_engine_state(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as con:
            row = con.execute("SELECT value FROM engine_state WHERE key=?", (key,)).fetchone()
            if not row:
                return default
            return str(row["value"])


    def set_engine_state_float(self, key: str, value: float) -> None:
        self.set_engine_state(key, f"{float(value):.6f}")

    def get_engine_state_float(self, key: str, default: float = 0.0) -> float:
        raw = self.get_engine_state(key)
        if raw is None:
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    def fetch_df(self, query: str):
        import pandas as pd

        with self.connect() as con:
            return pd.read_sql_query(query, con)
