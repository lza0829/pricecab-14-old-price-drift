from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 必须在导入 app 之前指向独立数据目录（config 在 import 时读取 DATA_DIR）。
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="pricecab-test-")

import pytest
from fastapi.testclient import TestClient

from app import config
from app.seed import seed_if_empty


@pytest.fixture()
def client():
    db = config.DB_PATH
    if db.exists():
        db.unlink()
    seed_if_empty()
    from app.main import app

    with TestClient(app) as c:
        yield c


def create_ticket(client, lines, effective_at="2026-11-16T10:00:00Z", title="测试改价"):
    r = client.post(
        "/api/tickets",
        json={"title": title, "effective_at": effective_at, "lines": lines},
    )
    assert r.status_code == 200, r.text
    return r.json()


def set_clock(client, clock="2026-11-16T12:00:00Z"):
    r = client.put("/api/apply/clock", json={"business_clock": clock})
    assert r.status_code == 200, r.text
    return r.json()


def apply_due(client):
    r = client.post("/api/apply/due")
    assert r.status_code == 200, r.text
    return r.json()


def adjust(client, sku_code, price):
    r = client.post("/api/prices/adjust", json={"sku_code": sku_code, "price": price})
    assert r.status_code == 200, r.text
    return r.json()


def book_price(client, sku_code):
    book = client.get("/api/prices/book").json()
    return next(r["current_price"] for r in book if r["sku_code"] == sku_code)


def history_of(client, sku_code):
    return client.get(f"/api/prices/history?sku_code={sku_code}").json()
