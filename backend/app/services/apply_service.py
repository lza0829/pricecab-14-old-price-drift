from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.repositories import settings as settings_repo
from app.repositories import tickets as ticket_repo
from app.repositories import prices as price_repo
from app.repositories import batches as batch_repo

# 漂移策略互斥，二选一（settings.drift_strategy）：
#   reject —— 遇漂移拒绝该单：不写该单任何新价，单保持 scheduled（可处理态）并记漂移原因；
#   allow  —— 允许漂移应用：写入新价，历史与批次明细同时记录期望旧价、实际旧价与漂移标记。
# 拒绝粒度固定为「单」：一张单任一行漂移即整单拒绝，同批其他单不受影响。
DRIFT_STRATEGIES = ("reject", "allow")
_EPS = 1e-9


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_clock() -> dict[str, Any]:
    conn = connect()
    try:
        return {
            "business_clock": settings_repo.get(conn, "business_clock"),
            "clock_label": settings_repo.get(conn, "clock_label"),
            "drift_strategy": _get_strategy(conn),
        }
    finally:
        conn.close()


def set_clock(clock_at: str) -> dict[str, Any]:
    clock_at = (clock_at or "").strip()
    if not clock_at:
        raise HTTPException(400, "业务钟不能为空")
    conn = connect(immediate=True)
    try:
        settings_repo.set_value(conn, "business_clock", clock_at)
        conn.commit()
        return get_clock()
    finally:
        conn.close()


def _get_strategy(conn) -> str:
    value = settings_repo.get(conn, "drift_strategy", "reject")
    return value if value in DRIFT_STRATEGIES else "reject"


def get_strategy() -> dict[str, str]:
    conn = connect()
    try:
        return {"drift_strategy": _get_strategy(conn)}
    finally:
        conn.close()


def set_strategy(value: str) -> dict[str, str]:
    value = (value or "").strip()
    if value not in DRIFT_STRATEGIES:
        raise HTTPException(400, f"漂移策略只能是 {' / '.join(DRIFT_STRATEGIES)}（互斥二选一）")
    conn = connect(immediate=True)
    try:
        settings_repo.set_value(conn, "drift_strategy", value)
        conn.commit()
        return {"drift_strategy": value}
    finally:
        conn.close()


def list_batches() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return batch_repo.list_batches(conn)
    finally:
        conn.close()


def list_batch_lines(batch_id: int) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return batch_repo.list_batch_lines(conn, batch_id)
    finally:
        conn.close()


def _precheck_snapshot(conn, due: list[dict[str, Any]]) -> dict[int, dict[str, float | None]]:
    """Phase 1 (read-only): snapshot current book prices for every due line.

    The snapshot is only a reading — it is never written back. At commit time
    each line is re-read under a write lock and compared against this snapshot
    to detect concurrent rewrites that landed while the batch was applying.
    """
    snap: dict[int, dict[str, float | None]] = {}
    for t in due:
        lines = ticket_repo.list_lines(conn, t["id"])
        snap[t["id"]] = {l["sku_code"]: price_repo.book_price(conn, l["sku_code"]) for l in lines}
    return snap


def _changed(snapshot: float | None, fresh: float | None) -> bool:
    if snapshot is None or fresh is None:
        return snapshot is not fresh
    return abs(fresh - snapshot) > _EPS


def _apply_one_ticket(
    t: dict[str, Any],
    snapshot: dict[str, float | None],
    *,
    strategy: str,
    batch_id: int,
    now: str,
) -> dict[str, Any]:
    """Apply (or drift-reject) one ticket in its own IMMEDIATE transaction.

    Commit-time re-check: every line's live price is re-read inside the write
    transaction; if it differs from the pre-check snapshot, a concurrent entry
    rewrote the tag mid-apply — drift is recomputed from the fresh reading and
    the line is marked rechecked. Writes use compare-and-swap so a stale
    reading can never be written as success.
    """
    conn = connect(immediate=True)
    try:
        status = ticket_repo.get_status(conn, t["id"])
        if status != "scheduled":
            conn.rollback()
            return {
                "ticket_no": t["ticket_no"],
                "decision": "skipped",
                "reason": f"提交时单状态已为 {status}（可能已被其他入口处理）",
                "lines": [],
            }

        lines = ticket_repo.list_lines(conn, t["id"])
        checked = []
        for line in lines:
            sku = line["sku_code"]
            expected_old = float(line["old_price"])
            fresh = price_repo.book_price(conn, sku)
            snap = snapshot.get(sku)
            rechecked = _changed(snap, fresh)
            drifted = fresh is None or abs(fresh - expected_old) > _EPS
            checked.append(
                {
                    "line": line,
                    "sku_code": sku,
                    "expected_old": expected_old,
                    "actual_old": fresh,
                    "new_price": float(line["new_price"]),
                    "rechecked": rechecked,
                    "drifted": drifted,
                }
            )

        drifted_lines = [c for c in checked if c["drifted"]]
        if strategy == "reject" and drifted_lines:
            # 拒绝该单：不写任何新价，单保持 scheduled，记漂移原因。
            reasons = []
            for c in checked:
                if c["drifted"]:
                    actual = "无价签" if c["actual_old"] is None else f"{c['actual_old']:.2f}"
                    reason = f"漂移：建单旧价 {c['expected_old']:.2f}，提交时价签 {actual}"
                    if c["rechecked"]:
                        reason += "（应用期间被并发改写，提交时再检测命中）"
                    reasons.append(f"{c['sku_code']} {reason}")
                else:
                    reason = "整单拒绝（同单他行漂移）"
                batch_repo.add_line(
                    conn,
                    batch_id=batch_id,
                    ticket_id=t["id"],
                    ticket_no=t["ticket_no"],
                    sku_code=c["sku_code"],
                    expected_old_price=c["expected_old"],
                    actual_old_price=c["actual_old"],
                    new_price=c["new_price"],
                    drifted=c["drifted"],
                    rechecked=c["rechecked"],
                    decision="rejected",
                    reason=reason,
                    created_at=now,
                )
            ticket_repo.mark_drift_rejected(conn, t["id"], drifted_at=now, reason="；".join(reasons))
            conn.commit()
            return {
                "ticket_no": t["ticket_no"],
                "decision": "rejected",
                "reason": "；".join(reasons),
                "lines": checked,
            }

        # 应用该单（无漂移，或策略为 allow 的漂移放行）。
        for c in checked:
            if c["actual_old"] is None:
                raise HTTPException(500, f"{c['sku_code']} 价签缺失，无法应用")
            affected = price_repo.cas_update_book(
                conn,
                sku_code=c["sku_code"],
                price=c["new_price"],
                ticket_no=t["ticket_no"],
                effective_at=t["effective_at"],
                updated_at=now,
                expected_current=c["actual_old"],
            )
            if affected != 1:
                # 提交瞬间价签被并发改写：回滚该单，绝不按过期读数写成功。
                conn.rollback()
                return _record_commit_conflict(t, checked, batch_id=batch_id, now=now)
            price_repo.insert_history(
                conn,
                sku_code=c["sku_code"],
                price=c["new_price"],
                ticket_no=t["ticket_no"],
                effective_at=t["effective_at"],
                recorded_at=now,
                expected_old_price=c["expected_old"],
                actual_old_price=c["actual_old"],
                drifted=c["drifted"],
            )
            batch_repo.add_line(
                conn,
                batch_id=batch_id,
                ticket_id=t["id"],
                ticket_no=t["ticket_no"],
                sku_code=c["sku_code"],
                expected_old_price=c["expected_old"],
                actual_old_price=c["actual_old"],
                new_price=c["new_price"],
                drifted=c["drifted"],
                rechecked=c["rechecked"],
                decision="applied",
                reason=("漂移放行" if c["drifted"] else None),
                created_at=now,
            )
        ticket_repo.mark_applied(conn, t["id"], applied_at=now, batch_id=batch_id)
        conn.commit()
        return {"ticket_no": t["ticket_no"], "decision": "applied", "lines": checked}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _record_commit_conflict(
    t: dict[str, Any],
    checked: list[dict[str, Any]],
    *,
    batch_id: int,
    now: str,
) -> dict[str, Any]:
    """CAS 兜底命中：提交瞬间价签被并发改写。该单不写任何新价，记为漂移拒绝。"""
    conn = connect(immediate=True)
    try:
        reason = "提交时检测到价签被并发改写，已回滚该单（未写任何新价）"
        for c in checked:
            batch_repo.add_line(
                conn,
                batch_id=batch_id,
                ticket_id=t["id"],
                ticket_no=t["ticket_no"],
                sku_code=c["sku_code"],
                expected_old_price=c["expected_old"],
                actual_old_price=c["actual_old"],
                new_price=c["new_price"],
                drifted=True,
                rechecked=True,
                decision="rejected",
                reason=reason,
                created_at=now,
            )
        ticket_repo.mark_drift_rejected(conn, t["id"], drifted_at=now, reason=reason)
        conn.commit()
        return {"ticket_no": t["ticket_no"], "decision": "rejected", "reason": reason, "lines": checked}
    finally:
        conn.close()


def apply_due() -> dict[str, Any]:
    """Apply all scheduled tickets whose effective_at <= business clock."""
    # Phase 1: read clock/strategy/due tickets and snapshot current prices.
    conn = connect()
    try:
        clock = settings_repo.get(conn, "business_clock")
        if not clock:
            raise HTTPException(400, "业务钟未设置")
        strategy = _get_strategy(conn)
        due = ticket_repo.due_tickets(conn, clock)
        snapshot = _precheck_snapshot(conn, due) if due else {}
    finally:
        conn.close()

    if not due:
        return {
            "applied": False,
            "message": "当前业务钟下没有到期改价单",
            "business_clock": clock,
            "strategy": strategy,
            "ticket_count": 0,
            "line_count": 0,
        }

    created = _now()
    conn = connect(immediate=True)
    try:
        batch_no = batch_repo.next_batch_no(conn)
        batch_id = batch_repo.create_batch(
            conn,
            batch_no=batch_no,
            clock_at=clock,
            ticket_count=0,
            line_count=0,
            status="running",
            created_at=created,
            strategy=strategy,
        )
        conn.commit()
    finally:
        conn.close()

    # Phase 2: each ticket commits in its own transaction, re-checking every
    # line's live price at commit time before writing.
    results = [
        _apply_one_ticket(t, snapshot.get(t["id"], {}), strategy=strategy, batch_id=batch_id, now=created)
        for t in due
    ]

    applied = [r for r in results if r["decision"] == "applied"]
    rejected = [r for r in results if r["decision"] == "rejected"]
    line_total = sum(len(r["lines"]) for r in applied)
    drift_total = sum(1 for r in applied for c in r["lines"] if c["drifted"])

    conn = connect(immediate=True)
    try:
        batch_repo.finalize_batch(
            conn,
            batch_id,
            ticket_count=len(due),
            line_count=line_total,
            applied_count=len(applied),
            rejected_count=len(rejected),
            drift_count=drift_total,
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "applied": True,
        "batch_no": batch_no,
        "batch_id": batch_id,
        "business_clock": clock,
        "strategy": strategy,
        "ticket_count": len(due),
        "applied_count": len(applied),
        "rejected_count": len(rejected),
        "line_count": line_total,
        "drift_count": drift_total,
        "tickets": [
            {
                "ticket_no": r["ticket_no"],
                "decision": r["decision"],
                **({"reason": r["reason"]} if r.get("reason") else {}),
            }
            for r in results
        ],
    }
