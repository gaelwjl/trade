"""P&L engine against a hand-worked golden ledger."""

import datetime as dt

import pandas as pd
import pytest

from tradelab.live import portfolio as pf
from tradelab.types import PriceFrame

D1, D2, D3, D4, D5 = (dt.date(2026, 1, d) for d in (2, 5, 6, 7, 8))  # sessions


def golden():
    fills = [
        pf.Fill(D1, "BUY", "AAA", 10, 100.0, fees=1.0, strategy="momentum_12_1", id=1),
        pf.Fill(D4, "SELL", "AAA", 5, 60.0, fees=1.0, id=2),
    ]
    flows = [
        pf.CashFlow(D1, "deposit", 10_000.0, id=1),
        pf.CashFlow(D5, "withdrawal", 1_000.0, id=2),
        pf.CashFlow(D5, "fee", 5.0, id=3),
    ]
    actions = [
        pf.CorporateAction("AAA", D3, "split", 2.0),
        pf.CorporateAction("AAA", D4, "dividend", 0.5),
    ]
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in (D1, D2, D3, D4, D5)])
    closes = PriceFrame(
        pd.DataFrame({"AAA": [100.0, 110.0, 56.0, 58.0, 58.0]}, index=idx)
    )
    return fills, flows, actions, closes, idx


def test_golden_ledger_snapshots():
    fills, flows, actions, closes, idx = golden()
    snap, book = pf.snapshots(fills, flows, actions, closes, idx)
    # buy: cost 1001 -> 10 @ 100.1; split 2:1 -> 20 @ 50.05; dividend 20 x 0.5 = 10;
    # sell 5 @ 60 - 1 fee = 299 proceeds, FIFO cost 250.25 -> realized 48.75
    assert list(snap.equity.round(2)) == [9999.0, 10099.0, 10119.0, 10178.0, 9173.0]
    assert snap.cash.iloc[-1] == pytest.approx(10_000 - 1001 + 10 + 299 - 1000 - 5)
    assert snap.realized_cum.iloc[-1] == pytest.approx(48.75)
    assert snap.unrealized.iloc[-1] == pytest.approx(15 * (58 - 50.05))
    assert snap.dividends_cum.iloc[-1] == pytest.approx(10.0)
    assert snap.deposits_cum.iloc[-1] == pytest.approx(9_000.0)
    assert list(snap.day_pnl.round(2)) == [-1.0, 100.0, 20.0, 59.0, -5.0]
    expected_twr = (
        (1 - 1 / 10_000)
        * (1 + 100 / 9_999)
        * (1 + 20 / 10_099)
        * (1 + 59 / 10_119)
        * (1 - 5 / 9_178)
    )
    assert snap.twr_index.iloc[-1] == pytest.approx(expected_twr)
    assert pf.identity_gap(snap).abs().max() < 1e-9
    assert book.qty("AAA") == pytest.approx(15)


def test_fifo_closes_oldest_lot_first():
    fills = [
        pf.Fill(D1, "BUY", "AAA", 10, 100.0, id=1),
        pf.Fill(D2, "BUY", "AAA", 10, 120.0, id=2),
        pf.Fill(D3, "SELL", "AAA", 15, 130.0, id=3),
    ]
    book = pf.replay(fills)
    assert book.realized_total == pytest.approx(15 * 130 - (10 * 100 + 5 * 120))
    [lot] = book.lots["AAA"]
    assert lot.qty == pytest.approx(5) and lot.cost_per_share == pytest.approx(120)


def test_oversell_rejected_and_split_counts():
    _, _, actions, _, _ = golden()
    buy = pf.Fill(D1, "BUY", "AAA", 10, 100.0, id=1)
    with pytest.raises(pf.LedgerError, match="only 20"):
        pf.validate([buy, pf.Fill(D4, "SELL", "AAA", 21, 60.0, id=2)], [], actions)
    pf.validate([buy, pf.Fill(D4, "SELL", "AAA", 20, 60.0, id=2)], [], actions)


def test_dividend_belongs_to_shares_held_before_the_ex_date_trades():
    fills = [pf.Fill(D4, "BUY", "AAA", 10, 50.0, id=1)]  # bought on the ex-date
    book = pf.replay(fills, [], [pf.CorporateAction("AAA", D4, "dividend", 0.5)])
    assert book.dividends == 0


def test_positions_and_strategy_holdings():
    fills, flows, actions, closes, idx = golden()
    _, book = pf.snapshots(fills, flows, actions, closes, idx)
    pos = pf.positions(book, {"AAA": 58.0}, equity=9173.0)
    assert pos.loc["AAA", "qty"] == pytest.approx(15)
    assert pos.loc["AAA", "avg_cost"] == pytest.approx(50.05)
    assert pos.loc["AAA", "strategies"] == "momentum_12_1"
    assert pf.strategy_holdings(book, "momentum_12_1") == pytest.approx({"AAA": 15})
    assert pf.strategy_holdings(book, "low_vol") == {}


def test_empty_ledger():
    snap, book = pf.snapshots(
        [], [], [], PriceFrame(pd.DataFrame()), pd.DatetimeIndex([])
    )
    assert len(snap) == 0 and book.cash == 0
