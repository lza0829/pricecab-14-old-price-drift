from __future__ import annotations

from app.db import connect
from app.repositories import prices as price_repo
from app.services import apply_service

from conftest import adjust, apply_due, book_price, create_ticket, history_of, set_clock


def test_apply_no_drift_success(client):
    """无漂移成功：到期单写入价签，历史带审计字段且 drifted=0，互证通过。"""
    t = create_ticket(client, [{"sku_code": "SKU-TEA-001", "new_price": 25.0}])
    assert t["lines"][0]["old_price"] == 28.0  # 建单时记下现行价
    set_clock(client)

    r = apply_due(client)
    assert r["applied"] is True
    assert r["strategy"] == "reject"
    assert r["applied_count"] == 1
    assert r["rejected_count"] == 0
    assert r["drift_count"] == 0
    assert r["line_count"] == 1

    assert book_price(client, "SKU-TEA-001") == 25.0

    head = history_of(client, "SKU-TEA-001")[0]
    assert head["price"] == 25.0
    assert head["source_ticket_no"] == t["ticket_no"]
    assert head["expected_old_price"] == 28.0
    assert head["actual_old_price"] == 28.0
    assert head["drifted"] == 0

    lines = client.get(f"/api/apply/batches/{r['batch_id']}/lines").json()
    assert len(lines) == 1
    assert lines[0]["decision"] == "applied"
    assert lines[0]["drifted"] == 0
    assert lines[0]["expected_old_price"] == 28.0
    assert lines[0]["actual_old_price"] == 28.0

    verify = client.get("/api/prices/verify").json()
    assert verify["ok"] is True and verify["mismatches"] == []

    tickets = {x["ticket_no"]: x for x in client.get("/api/tickets").json()}
    assert tickets[t["ticket_no"]]["status"] == "applied"
    assert tickets[t["ticket_no"]]["last_drift_reason"] is None


def test_drift_reject_strategy(client):
    """漂移 + reject：整单不写任何新价，单保持可处理态并记漂移原因；修复后可重新应用。"""
    t = create_ticket(client, [{"sku_code": "SKU-TEA-001", "new_price": 25.0}])
    adjust(client, "SKU-TEA-001", 26.0)  # 其他入口先改了价 → 建单旧价 28 已漂移
    set_clock(client)

    r = apply_due(client)
    assert r["applied"] is True
    assert r["applied_count"] == 0
    assert r["rejected_count"] == 1
    assert r["line_count"] == 0
    assert r["tickets"][0]["decision"] == "rejected"
    assert "漂移" in r["tickets"][0]["reason"]

    # 拒绝策略下不得写出该单新价：价签仍是手动调价后的 26.0
    assert book_price(client, "SKU-TEA-001") == 26.0
    prices = [h["price"] for h in history_of(client, "SKU-TEA-001")]
    assert 25.0 not in prices

    # 单保持可处理态（scheduled）并记漂移原因
    tickets = {x["ticket_no"]: x for x in client.get("/api/tickets").json()}
    row = tickets[t["ticket_no"]]
    assert row["status"] == "scheduled"
    assert "漂移" in row["last_drift_reason"]
    assert row["last_drift_at"]

    # 批次明细记录期望/实际旧价与漂移标记
    lines = client.get(f"/api/apply/batches/{r['batch_id']}/lines").json()
    assert len(lines) == 1
    assert lines[0]["decision"] == "rejected"
    assert lines[0]["drifted"] == 1
    assert lines[0]["expected_old_price"] == 28.0
    assert lines[0]["actual_old_price"] == 26.0

    # 价签被改回建单旧价后，同一单可重新应用成功（可处理态语义）
    adjust(client, "SKU-TEA-001", 28.0)
    r2 = apply_due(client)
    assert r2["applied_count"] == 1
    assert book_price(client, "SKU-TEA-001") == 25.0
    tickets = {x["ticket_no"]: x for x in client.get("/api/tickets").json()}
    assert tickets[t["ticket_no"]]["status"] == "applied"
    assert tickets[t["ticket_no"]]["last_drift_reason"] is None


def test_drift_allow_strategy(client):
    """漂移 + allow：写入新价，历史与批次明细同时记录期望旧价、实际旧价与漂移标记。"""
    assert client.put("/api/apply/strategy", json={"drift_strategy": "allow"}).status_code == 200
    t = create_ticket(client, [{"sku_code": "SKU-TEA-001", "new_price": 25.0}])
    adjust(client, "SKU-TEA-001", 26.0)
    set_clock(client)

    r = apply_due(client)
    assert r["strategy"] == "allow"
    assert r["applied_count"] == 1
    assert r["rejected_count"] == 0
    assert r["drift_count"] == 1  # 漂移放行数

    assert book_price(client, "SKU-TEA-001") == 25.0

    head = history_of(client, "SKU-TEA-001")[0]
    assert head["price"] == 25.0
    assert head["expected_old_price"] == 28.0  # 建单时记下的旧价
    assert head["actual_old_price"] == 26.0    # 提交时价签实际现行价
    assert head["drifted"] == 1

    lines = client.get(f"/api/apply/batches/{r['batch_id']}/lines").json()
    assert lines[0]["decision"] == "applied"
    assert lines[0]["drifted"] == 1
    assert lines[0]["expected_old_price"] == 28.0
    assert lines[0]["actual_old_price"] == 26.0

    # 销售对账仍按正式历史链：漂移放行行就在 price_history 链上，对账照常运行
    rec = client.get("/api/reconcile/sales").json()
    assert rec["sale_count"] == 5
    s1001 = next(x for x in rec["rows"] if x["sale_no"] == "S-1001")  # 08:10 成交，早于生效时刻
    assert s1001["expected_price"] == 28.0
    assert s1001["status"] == "match"

    verify = client.get("/api/prices/verify").json()
    assert verify["ok"] is True


def test_reject_granularity_is_ticket(client):
    """拒绝粒度固定为「单」：同单干净行也不写；同批其他干净单照常应用。"""
    mixed = create_ticket(
        client,
        [
            {"sku_code": "SKU-TEA-001", "new_price": 25.0},  # 建单旧价 28，应用前将被改走
            {"sku_code": "SKU-SNK-011", "new_price": 11.0},  # 干净行（建单旧价 12.5）
        ],
        title="混合单",
    )
    clean = create_ticket(client, [{"sku_code": "SKU-DAY-021", "new_price": 42.0}], title="干净单")
    adjust(client, "SKU-TEA-001", 26.0)  # 建单后其他入口改价 → 混合单第一行漂移
    set_clock(client)

    r = apply_due(client)
    assert r["applied_count"] == 1
    assert r["rejected_count"] == 1

    # 混合单整单拒绝：干净行 SKU-SNK-011 也不得写出
    assert book_price(client, "SKU-TEA-001") == 26.0
    assert book_price(client, "SKU-SNK-011") == 12.5
    # 同批干净单不受影响
    assert book_price(client, "SKU-DAY-021") == 42.0

    lines = client.get(f"/api/apply/batches/{r['batch_id']}/lines").json()
    mixed_lines = [l for l in lines if l["ticket_no"] == mixed["ticket_no"]]
    assert len(mixed_lines) == 2
    assert all(l["decision"] == "rejected" for l in mixed_lines)
    drifted = {l["sku_code"]: l["drifted"] for l in mixed_lines}
    assert drifted == {"SKU-TEA-001": 1, "SKU-SNK-011": 0}
    clean_lines = [l for l in lines if l["ticket_no"] == clean["ticket_no"]]
    assert clean_lines[0]["decision"] == "applied"


def test_commit_time_recheck_reject(client):
    """应用中价签被并发改写（预检后、提交前）→ 提交时再检测命中，reject 下拒单且不写价。"""
    create_ticket(client, [{"sku_code": "SKU-TEA-001", "new_price": 25.0}])
    set_clock(client)

    original = apply_service._precheck_snapshot

    def wrapped(conn, due):
        snap = original(conn, due)
        # 其他入口在预检之后、本单提交之前改写价签（独立连接模拟并发入口）
        c = connect(immediate=True)
        try:
            price_repo.upsert_book(
                conn=c,
                sku_code="SKU-TEA-001",
                price=27.5,
                ticket_no=None,
                effective_at="2026-11-16T11:30:00Z",
                updated_at="2026-11-16T11:30:00Z",
                expected_old_price=28.0,
                actual_old_price=28.0,
                drifted=False,
            )
            c.commit()
        finally:
            c.close()
        return snap  # 快照已过期：仍是 28.0

    apply_service._precheck_snapshot = wrapped
    try:
        r = apply_due(client)
    finally:
        apply_service._precheck_snapshot = original

    assert r["rejected_count"] == 1
    assert r["applied_count"] == 0
    # 不得用过期读数写成功：价签是并发写入的 27.5，不是该单的 25.0
    assert book_price(client, "SKU-TEA-001") == 27.5

    lines = client.get(f"/api/apply/batches/{r['batch_id']}/lines").json()
    assert lines[0]["decision"] == "rejected"
    assert lines[0]["rechecked"] == 1          # 提交时再检测命中并发改写
    assert lines[0]["drifted"] == 1
    assert lines[0]["actual_old_price"] == 27.5  # 以提交时新读数为准，非过期快照
    assert "并发改写" in lines[0]["reason"]


def test_commit_time_recheck_allow(client):
    """并发改写 + allow：放行但审计字段取提交时新读数（实际旧价=27.5），不用过期快照。"""
    assert client.put("/api/apply/strategy", json={"drift_strategy": "allow"}).status_code == 200
    create_ticket(client, [{"sku_code": "SKU-TEA-001", "new_price": 25.0}])
    set_clock(client)

    original = apply_service._precheck_snapshot

    def wrapped(conn, due):
        snap = original(conn, due)
        c = connect(immediate=True)
        try:
            price_repo.upsert_book(
                conn=c,
                sku_code="SKU-TEA-001",
                price=27.5,
                ticket_no=None,
                effective_at="2026-11-16T11:30:00Z",
                updated_at="2026-11-16T11:30:00Z",
                expected_old_price=28.0,
                actual_old_price=28.0,
                drifted=False,
            )
            c.commit()
        finally:
            c.close()
        return snap

    apply_service._precheck_snapshot = wrapped
    try:
        r = apply_due(client)
    finally:
        apply_service._precheck_snapshot = original

    assert r["applied_count"] == 1
    assert r["drift_count"] == 1
    assert book_price(client, "SKU-TEA-001") == 25.0

    head = history_of(client, "SKU-TEA-001")[0]
    assert head["expected_old_price"] == 28.0
    assert head["actual_old_price"] == 27.5  # 提交时再检测后的新读数
    assert head["drifted"] == 1

    lines = client.get(f"/api/apply/batches/{r['batch_id']}/lines").json()
    assert lines[0]["rechecked"] == 1
    assert lines[0]["actual_old_price"] == 27.5

    verify = client.get("/api/prices/verify").json()
    assert verify["ok"] is True


def test_strategy_is_validated(client):
    """漂移策略互斥二选一，非法值拒绝。"""
    r = client.put("/api/apply/strategy", json={"drift_strategy": "bogus"})
    assert r.status_code == 400
    assert client.get("/api/apply/strategy").json()["drift_strategy"] == "reject"
    assert client.put("/api/apply/strategy", json={"drift_strategy": "allow"}).status_code == 200
    assert client.get("/api/apply/strategy").json()["drift_strategy"] == "allow"


def test_verify_book_detects_inconsistency(client):
    """价签来源互证：价签本与历史链头不一致能被检出。"""
    assert client.get("/api/prices/verify").json()["ok"] is True
    conn = connect(immediate=True)
    try:
        conn.execute(
            "UPDATE price_book SET current_price=999.0 WHERE sku_code='SKU-TEA-001'"
        )
        conn.commit()
    finally:
        conn.close()
    v = client.get("/api/prices/verify").json()
    assert v["ok"] is False
    assert v["mismatches"][0]["sku_code"] == "SKU-TEA-001"
