from __future__ import annotations

import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

DB_PATH = Path("data/autotrade.db")
logger = logging.getLogger(__name__)
REPORT_TABLE_ALLOWLIST: dict[str, dict[str, str | None]] = {
    "signals": {"default_where": None},
    "trades": {"default_where": "status='CLOSED'"},
    "performance_reports": {"default_where": None},
    "daily_performance": {"default_where": None},
    "report_cache": {"default_where": None},
}


class Database:
    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not os.access(self.path.parent, os.W_OK):
            logger.warning("event=DB_PERMISSION_WARN path=%s writable=false", self.path.parent)
        self._init_db()

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=5.0)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA busy_timeout=5000")
        except Exception:
            pass
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

    def list_tables(self) -> set[str]:
        with self.connect() as con:
            rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {str(r["name"]) for r in rows}

    def get_table_columns(self, table_name: str) -> set[str]:
        with self.connect() as con:
            rows = con.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(r["name"]).lower() for r in rows}

    @staticmethod
    def _dry_filter_clause(columns: set[str]) -> str | None:
        if "is_dry_run" in columns:
            return "is_dry_run = 1"
        if "dry_run" in columns:
            return "dry_run = 1"
        if "mode" in columns:
            return "UPPER(mode) IN ('DRY','DRY-RUN','DRY_RUN','PAPER')"
        return None

    def clear_report_data(self, only_dry: bool = True, vacuum: bool = False) -> dict[str, Any]:
        existing = self.list_tables()
        deleted: dict[str, int] = {}
        skipped: list[str] = []

        if not any(t in existing for t in REPORT_TABLE_ALLOWLIST):
            return {
                "deleted": {},
                "skipped": list(REPORT_TABLE_ALLOWLIST.keys()),
                "vacuum": False,
                "only_dry": only_dry,
                "message": "nothing to delete",
            }

        def _run_delete() -> None:
            with self.connect() as con:
                for table, spec in REPORT_TABLE_ALLOWLIST.items():
                    if table not in existing:
                        skipped.append(table)
                        continue

                    cols = self.get_table_columns(table)
                    where_clause = spec["default_where"]
                    dry_clause = self._dry_filter_clause(cols) if only_dry else None

                    if dry_clause:
                        where_clause = dry_clause

                    sql = f"DELETE FROM {table}"
                    if where_clause:
                        sql += f" WHERE {where_clause}"

                    cur = con.execute(sql)
                    deleted[table] = int(cur.rowcount or 0)

                if vacuum:
                    con.execute("VACUUM")

        for attempt in range(3):
            try:
                _run_delete()
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 2:
                    raise
                time.sleep(0.2 * (attempt + 1))

        message = "nothing to delete" if sum(deleted.values()) == 0 else "deleted"
        return {
            "deleted": deleted,
            "skipped": skipped,
            "vacuum": vacuum,
            "only_dry": only_dry,
            "message": message,
        }

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

    def fetch_recent_trades(self, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        with self.connect() as con:
            rows = con.execute(
                """
                SELECT id, entry_time, exit_time, symbol, qty, entry_price, exit_price,
                       pnl, pnl_pct, fees, reason_enter, reason_exit, status
                FROM trades
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]
