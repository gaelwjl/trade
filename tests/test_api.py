import datetime as dt

import pytest
from fastapi.testclient import TestClient

from tradelab.api.app import create_app
from tradelab.live.suggestions import Order
from tradelab.store import daily as daily_store
from tradelab.store import strategies as strat_store
from tradelab.store.db import session_scope


@pytest.fixture
def client(engine):
    return TestClient(create_app(engine))


def test_fill_and_cash_flow(client):
    assert (
        client.post(
            "/api/cash", json={"date": "2026-01-05", "kind": "deposit", "amount": 5000}
        ).status_code
        == 201
    )
    r = client.post(
        "/api/fills",
        json={
            "trade_date": "2026-01-05",
            "side": "BUY",
            "symbol": "zzzz",
            "qty": 10,
            "price": 100,
            "fees": 1,
        },
    )
    assert r.status_code == 201 and r.json()["symbol"] == "ZZZZ"
    bad = client.post(
        "/api/fills",
        json={
            "trade_date": "2026-01-06",
            "side": "SELL",
            "symbol": "ZZZZ",
            "qty": 11,
            "price": 100,
        },
    )
    assert bad.status_code == 400 and "only 10" in bad.json()["detail"]
    p = client.get("/api/portfolio").json()
    assert p["positions"][0]["symbol"] == "ZZZZ" and p["positions"][0]["qty"] == 10
    assert p["missing_prices"] == ["ZZZZ"]  # never fetched: valued at the fill price
    assert p["totals"]["cash"] == pytest.approx(5000 - 1001)
    fid = client.get("/api/fills").json()[0]["id"]
    assert client.patch(f"/api/fills/{fid}", json={"price": 90}).status_code == 200
    assert client.delete(f"/api/fills/{fid}").status_code == 200
    assert [a["action"] for a in client.get("/api/audit").json()][:3] == [
        "delete",
        "update",
        "create",
    ]
    assert client.delete("/api/fills/9999").status_code == 404


def test_today_without_anything(client):
    t = client.get("/api/today").json()
    assert (
        t["active"] is None and t["suggestions"] == [] and isinstance(t["alerts"], list)
    )
    assert client.get("/api/strategy").json() == {"active": None}


def test_suggestion_fill_links_and_completes(client, engine):
    with session_scope(engine) as s:
        cfg = strat_store.config(s, "trend_200d")
        order = Order("ZZZZ", "BUY", "BUY", 5, 10.0, 0.5, 0, "enters the target")
        [sug] = daily_store.replace_suggestions(
            s, dt.date(2026, 1, 5), dt.date(2026, 1, 6), cfg.id, [order]
        )
        sid = sug.id
    r = client.post(f"/api/suggestions/{sid}/fill", json={"price": 10.2})
    assert r.status_code == 201
    body = r.json()
    assert (
        body["fill"]["suggestion_id"] == sid
        and body["fill"]["strategy"] == "trend_200d"
    )
    assert (
        body["fill"]["trade_date"] == "2026-01-06"
        and body["suggestion"]["status"] == "done"
    )
    assert client.post(f"/api/suggestions/{sid}/dismiss").json()["status"] == "done"


def test_backtest_submit_validation_and_promote_guard(client):
    assert client.post("/api/backtests", json={"strategy": "nope"}).status_code == 400
    made = client.post(
        "/api/backtests",
        json={"strategy": "trend_200d", "grid": {"window": [100, 200]}},
    ).json()
    assert len(made) == 2 and {m["label"] for m in made} == {
        "trend_200d(window=100)",
        "trend_200d(window=200)",
    }
    assert all(m["status"] == "queued" for m in made)
    r = client.post(
        f"/api/backtests/{made[0]['id']}/promote", json={"capital_usd": 10000}
    )
    assert r.status_code == 400 and "finished" in r.json()["detail"]


def test_strategies_watchlist_and_pages(client):
    names = [s["name"] for s in client.get("/api/strategies").json()]
    assert "momentum_12_1" in names and "qqq_50_200_trend" in names
    assert client.put(
        "/api/watchlist", json={"symbols": ["nvda", "AAPL", ""]}
    ).json() == ["AAPL", "NVDA"]
    assert client.get("/api/watchlist").json() == ["AAPL", "NVDA"]
    for path in ["/", "/portfolio", "/strategy", "/backtests", "/settings"]:
        r = client.get(path)
        assert r.status_code == 200 and "topnav" in r.text
