from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.repositories import skus as sku_repo
from app.repositories import prices as price_repo
from app.repositories import settings as settings_repo


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def adjust_price(body: dict[str, Any]) -> dict[str, Any]:
    """手动调价（改价单之外的另一入口）：直接改写价签并落历史。

    该入口正是「应用过程中其他入口又改同商品价签」的来源之一——它会让
    建单时记下的 old_price 与价签本现行价拉开，应用时由漂移检测兜底。
    """
    sku_code = (body.get("sku_code") or "").strip()
    price = body.get("price")
    reason = (body.get("reason") or "").strip() or None
    if not sku_code:
        raise HTTPException(400, "sku_code 必填")
    if price is None:
        raise HTTPException(400, "price 必填")
    price = float(price)
    if price < 0:
        raise HTTPException(400, "price 不能为负")

    conn = connect(immediate=True)
    try:
        sku = sku_repo.get_by_code(conn, sku_code)
        if not sku:
            raise HTTPException(400, f"未知商品 {sku_code}")
        prev = price_repo.book_price(conn, sku_code)
        if prev is None:
            prev = float(sku["list_price"])
        # 手动调价立即生效：生效时刻取当前业务钟（业务时间），记录时间取真实时钟。
        effective_at = settings_repo.get(conn, "business_clock") or _now()
        now = _now()
        price_repo.upsert_book(
            conn,
            sku_code=sku_code,
            price=price,
            ticket_no=None,
            effective_at=effective_at,
            updated_at=now,
            expected_old_price=prev,
            actual_old_price=prev,
            drifted=False,
        )
        conn.commit()
        return {
            "sku_code": sku_code,
            "old_price": prev,
            "new_price": price,
            "effective_at": effective_at,
            "reason": reason,
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def verify_book() -> dict[str, Any]:
    """价签来源互证：价签本现行价/来源单与历史链头交叉核对。"""
    conn = connect()
    try:
        return price_repo.verify_book(conn)
    finally:
        conn.close()
