from __future__ import annotations

import sqlite3
from typing import Any

from app.config import DB_PATH


def connect(*, immediate: bool = False) -> sqlite3.Connection:
    # timeout：并发入口（应用到期 / 手动调价）互相等锁时的 busy 上限，
    # 默认 5s 在多写者竞争下容易直接抛 database is locked。
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if immediate:
        conn.execute("BEGIN IMMEDIATE")
    return conn


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None
