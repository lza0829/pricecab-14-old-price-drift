from __future__ import annotations

import sqlite3
from typing import Any


def list_book(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        '''
        SELECT b.*, s.title, s.category
        FROM price_book b
        JOIN skus s ON s.sku_code = b.sku_code
        ORDER BY b.sku_code
        '''
    ).fetchall()
    return [dict(r) for r in rows]


def book_price(conn: sqlite3.Connection, sku_code: str) -> float | None:
    """Current price-book value for a SKU (None if the SKU has no book row)."""
    row = conn.execute(
        "SELECT current_price FROM price_book WHERE sku_code=?", (sku_code,)
    ).fetchone()
    return float(row["current_price"]) if row else None


def upsert_book(
    conn: sqlite3.Connection,
    *,
    sku_code: str,
    price: float,
    ticket_no: str | None,
    effective_at: str,
    updated_at: str,
    expected_old_price: float | None = None,
    actual_old_price: float | None = None,
    drifted: bool = False,
) -> None:
    conn.execute(
        '''
        INSERT INTO price_book(sku_code, current_price, source_ticket_no, effective_at, updated_at)
        VALUES (?,?,?,?,?)
        ON CONFLICT(sku_code) DO UPDATE SET
          current_price=excluded.current_price,
          source_ticket_no=excluded.source_ticket_no,
          effective_at=excluded.effective_at,
          updated_at=excluded.updated_at
        ''',
        (sku_code, price, ticket_no, effective_at, updated_at),
    )
    insert_history(
        conn,
        sku_code=sku_code,
        price=price,
        ticket_no=ticket_no,
        effective_at=effective_at,
        recorded_at=updated_at,
        expected_old_price=expected_old_price,
        actual_old_price=actual_old_price,
        drifted=drifted,
    )


def cas_update_book(
    conn: sqlite3.Connection,
    *,
    sku_code: str,
    price: float,
    ticket_no: str,
    effective_at: str,
    updated_at: str,
    expected_current: float,
) -> int:
    """Compare-and-swap write of the price book.

    Only updates when the live price still equals ``expected_current`` (the
    value re-read at commit time); returns the affected row count so the
    caller can detect a concurrent rewrite instead of succeeding on a stale
    reading.
    """
    cur = conn.execute(
        '''
        UPDATE price_book
        SET current_price=?, source_ticket_no=?, effective_at=?, updated_at=?
        WHERE sku_code=? AND current_price=?
        ''',
        (price, ticket_no, effective_at, updated_at, sku_code, expected_current),
    )
    return cur.rowcount


def insert_history(
    conn: sqlite3.Connection,
    *,
    sku_code: str,
    price: float,
    ticket_no: str | None,
    effective_at: str,
    recorded_at: str,
    expected_old_price: float | None = None,
    actual_old_price: float | None = None,
    drifted: bool = False,
) -> None:
    conn.execute(
        '''
        INSERT INTO price_history(sku_code, price, source_ticket_no, effective_at, recorded_at,
                                  expected_old_price, actual_old_price, drifted)
        VALUES (?,?,?,?,?,?,?,?)
        ''',
        (
            sku_code,
            price,
            ticket_no,
            effective_at,
            recorded_at,
            expected_old_price,
            actual_old_price,
            1 if drifted else 0,
        ),
    )


def list_history(conn: sqlite3.Connection, sku_code: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    if sku_code:
        rows = conn.execute(
            "SELECT * FROM price_history WHERE sku_code=? ORDER BY id DESC LIMIT ?",
            (sku_code, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM price_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def price_at(conn: sqlite3.Connection, sku_code: str, at: str) -> float | None:
    row = conn.execute(
        '''
        SELECT price FROM price_history
        WHERE sku_code=? AND effective_at <= ?
        ORDER BY effective_at DESC, id DESC
        LIMIT 1
        ''',
        (sku_code, at),
    ).fetchone()
    return float(row["price"]) if row else None


def verify_book(conn: sqlite3.Connection) -> dict[str, Any]:
    """价签来源互证：价签本现行价/来源单必须与历史链头一致。

    价签本是「最后写入胜出」，因此链头按记录顺序（id 最大）取；
    与销售对账按 effective_at 的时点查询是两条独立路径。
    """
    rows = conn.execute(
        '''
        SELECT b.sku_code, b.current_price, b.source_ticket_no,
               h.price AS head_price, h.source_ticket_no AS head_source, h.id AS head_id
        FROM price_book b
        LEFT JOIN price_history h ON h.id = (
          SELECT id FROM price_history
          WHERE sku_code = b.sku_code
          ORDER BY id DESC
          LIMIT 1
        )
        ORDER BY b.sku_code
        '''
    ).fetchall()
    mismatches = []
    for r in rows:
        problems = []
        if r["head_id"] is None:
            problems.append("历史链缺失")
        else:
            if abs(float(r["current_price"]) - float(r["head_price"])) > 1e-9:
                problems.append("现行价与历史链头价格不一致")
            if (r["source_ticket_no"] or None) != (r["head_source"] or None):
                problems.append("来源单与历史链头不一致")
        if problems:
            mismatches.append(
                {
                    "sku_code": r["sku_code"],
                    "current_price": r["current_price"],
                    "source_ticket_no": r["source_ticket_no"],
                    "head_price": r["head_price"],
                    "head_source": r["head_source"],
                    "problems": problems,
                }
            )
    return {"ok": not mismatches, "checked": len(rows), "mismatches": mismatches}
