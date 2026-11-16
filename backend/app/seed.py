from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.db import connect


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def init_schema(conn) -> None:
    conn.executescript(
        '''
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS skus (
          id INTEGER PRIMARY KEY,
          sku_code TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          category TEXT NOT NULL,
          list_price REAL NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS price_tickets (
          id INTEGER PRIMARY KEY,
          ticket_no TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          status TEXT NOT NULL,
          effective_at TEXT NOT NULL,
          note TEXT,
          created_at TEXT NOT NULL,
          applied_at TEXT,
          apply_batch_id INTEGER,
          last_drift_at TEXT,
          last_drift_reason TEXT
        );
        CREATE TABLE IF NOT EXISTS price_ticket_lines (
          id INTEGER PRIMARY KEY,
          ticket_id INTEGER NOT NULL REFERENCES price_tickets(id) ON DELETE CASCADE,
          sku_code TEXT NOT NULL,
          old_price REAL NOT NULL,
          new_price REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS apply_batches (
          id INTEGER PRIMARY KEY,
          batch_no TEXT NOT NULL UNIQUE,
          clock_at TEXT NOT NULL,
          ticket_count INTEGER NOT NULL,
          line_count INTEGER NOT NULL,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL,
          strategy TEXT,
          applied_count INTEGER NOT NULL DEFAULT 0,
          rejected_count INTEGER NOT NULL DEFAULT 0,
          drift_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS apply_batch_lines (
          id INTEGER PRIMARY KEY,
          batch_id INTEGER NOT NULL REFERENCES apply_batches(id) ON DELETE CASCADE,
          ticket_id INTEGER NOT NULL,
          ticket_no TEXT NOT NULL,
          sku_code TEXT NOT NULL,
          expected_old_price REAL NOT NULL,
          actual_old_price REAL,
          new_price REAL NOT NULL,
          drifted INTEGER NOT NULL DEFAULT 0,
          rechecked INTEGER NOT NULL DEFAULT 0,
          decision TEXT NOT NULL,
          reason TEXT,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS price_book (
          sku_code TEXT PRIMARY KEY,
          current_price REAL NOT NULL,
          source_ticket_no TEXT,
          effective_at TEXT,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS price_history (
          id INTEGER PRIMARY KEY,
          sku_code TEXT NOT NULL,
          price REAL NOT NULL,
          source_ticket_no TEXT,
          effective_at TEXT NOT NULL,
          recorded_at TEXT NOT NULL,
          expected_old_price REAL,
          actual_old_price REAL,
          drifted INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sales_fixture (
          id INTEGER PRIMARY KEY,
          sale_no TEXT NOT NULL UNIQUE,
          sku_code TEXT NOT NULL,
          sold_at TEXT NOT NULL,
          unit_price REAL NOT NULL,
          qty INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hold_applies (
          id INTEGER PRIMARY KEY,
          ticket_id INTEGER,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        '''
    )


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    """Add a column to an existing table if it is missing (cheap migration)."""
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def migrate(conn) -> None:
    """Bring databases created by older versions up to the current schema."""
    _ensure_column(conn, "price_tickets", "last_drift_at", "last_drift_at TEXT")
    _ensure_column(conn, "price_tickets", "last_drift_reason", "last_drift_reason TEXT")
    _ensure_column(conn, "price_history", "expected_old_price", "expected_old_price REAL")
    _ensure_column(conn, "price_history", "actual_old_price", "actual_old_price REAL")
    _ensure_column(conn, "price_history", "drifted", "drifted INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "apply_batches", "strategy", "strategy TEXT")
    _ensure_column(conn, "apply_batches", "applied_count", "applied_count INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "apply_batches", "rejected_count", "rejected_count INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "apply_batches", "drift_count", "drift_count INTEGER NOT NULL DEFAULT 0")


def seed_if_empty() -> None:
    conn = connect()
    try:
        init_schema(conn)
        migrate(conn)
        # 漂移策略互斥：reject（遇漂移拒单）/ allow（允许漂移应用），默认 reject。
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES ('drift_strategy', 'reject')"
        )
        n = conn.execute("SELECT COUNT(*) c FROM skus").fetchone()["c"]
        if n:
            conn.commit()
            return

        skus = json.loads((FIXTURES / "skus.json").read_text(encoding="utf-8"))
        sales = json.loads((FIXTURES / "sales.json").read_text(encoding="utf-8"))
        now = _now()
        # 初始价自业务日 0 点起生效，让当日销售夹具都能对上正式历史链。
        day_start = "2026-11-16T00:00:00Z"
        for s in skus:
            conn.execute(
                "INSERT INTO skus(sku_code, title, category, list_price, enabled) VALUES (?,?,?,?,1)",
                (s["sku_code"], s["title"], s["category"], float(s["list_price"])),
            )
            conn.execute(
                "INSERT INTO price_book(sku_code, current_price, source_ticket_no, effective_at, updated_at) VALUES (?,?,NULL,?,?)",
                (s["sku_code"], float(s["list_price"]), day_start, now),
            )
            conn.execute(
                "INSERT INTO price_history(sku_code, price, source_ticket_no, effective_at, recorded_at) VALUES (?,?,NULL,?,?)",
                (s["sku_code"], float(s["list_price"]), day_start, now),
            )
        for row in sales:
            conn.execute(
                "INSERT INTO sales_fixture(sale_no, sku_code, sold_at, unit_price, qty) VALUES (?,?,?,?,?)",
                (row["sale_no"], row["sku_code"], row["sold_at"], float(row["unit_price"]), int(row["qty"])),
            )
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('business_clock', ?)",
            ("2026-11-16T09:00:00Z",),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('clock_label', ?)",
            ("业务钟（可拨，用于判断改价是否到期）",),
        )
        conn.commit()
    finally:
        conn.close()
