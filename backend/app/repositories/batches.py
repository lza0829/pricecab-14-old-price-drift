from __future__ import annotations

import sqlite3
from typing import Any


def next_batch_no(conn: sqlite3.Connection) -> str:
    n = conn.execute("SELECT COUNT(*) c FROM apply_batches").fetchone()["c"] + 1
    return f"AB-{n:04d}"


def create_batch(
    conn: sqlite3.Connection,
    *,
    batch_no: str,
    clock_at: str,
    ticket_count: int,
    line_count: int,
    status: str,
    created_at: str,
    strategy: str,
) -> int:
    cur = conn.execute(
        '''
        INSERT INTO apply_batches(batch_no, clock_at, ticket_count, line_count, status, created_at,
                                  strategy, applied_count, rejected_count, drift_count)
        VALUES (?,?,?,?,?,?,?,0,0,0)
        ''',
        (batch_no, clock_at, ticket_count, line_count, status, created_at, strategy),
    )
    return int(cur.lastrowid)


def finalize_batch(
    conn: sqlite3.Connection,
    batch_id: int,
    *,
    ticket_count: int,
    line_count: int,
    applied_count: int,
    rejected_count: int,
    drift_count: int,
    status: str = "done",
) -> None:
    conn.execute(
        '''
        UPDATE apply_batches
        SET ticket_count=?, line_count=?, applied_count=?, rejected_count=?, drift_count=?, status=?
        WHERE id=?
        ''',
        (ticket_count, line_count, applied_count, rejected_count, drift_count, status, batch_id),
    )


def add_line(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    ticket_id: int,
    ticket_no: str,
    sku_code: str,
    expected_old_price: float,
    actual_old_price: float | None,
    new_price: float,
    drifted: bool,
    rechecked: bool,
    decision: str,
    reason: str | None,
    created_at: str,
) -> int:
    cur = conn.execute(
        '''
        INSERT INTO apply_batch_lines(batch_id, ticket_id, ticket_no, sku_code,
                                      expected_old_price, actual_old_price, new_price,
                                      drifted, rechecked, decision, reason, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ''',
        (
            batch_id,
            ticket_id,
            ticket_no,
            sku_code,
            expected_old_price,
            actual_old_price,
            new_price,
            1 if drifted else 0,
            1 if rechecked else 0,
            decision,
            reason,
            created_at,
        ),
    )
    return int(cur.lastrowid)


def list_batches(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM apply_batches ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_batch_lines(conn: sqlite3.Connection, batch_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM apply_batch_lines WHERE batch_id=? ORDER BY id",
        (batch_id,),
    ).fetchall()
    return [dict(r) for r in rows]
