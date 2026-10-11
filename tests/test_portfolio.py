import pandas as pd
import pytest

from tradelab.live import portfolio as pf
from tradelab.types import TradeLedger


def ledger(*rows) -> TradeLedger:
    return TradeLedger(pd.DataFrame([pf.make_trade(*r[:4], date=r[4], strategy=r[5] if len(r) > 5 else "") for r in rows],
                                    columns=pf.COLUMNS))


def test_average_cost_and_realized_pnl():
    led = ledger(("BUY", "AAA", 10, 100.0, "2026-01-02", "momentum_12_1"), ("BUY", "AAA", 10, 120.0, "2026-01-05"),
                 ("SELL", "AAA", 5, 130.0, "2026-01-06"))
    pos, realized = pf.positions(led)
    assert pos.loc["AAA", "qty"] == 15 and pos.loc["AAA", "avg_cost"] == pytest.approx(110.0)
    assert realized == pytest.approx(5 * (130 - 110))
    assert pos.loc["AAA", "strategies"] == "momentum_12_1"


def test_closed_position_disappears_and_reopen_resets_tags():
    led = ledger(("BUY", "AAA", 1, 10.0, "2026-01-02", "low_vol"), ("SELL", "AAA", 1, 12.0, "2026-01-03"),
                 ("BUY", "AAA", 2, 11.0, "2026-01-04", "trend_200d"))
    pos, realized = pf.positions(led)
    assert pos.loc["AAA", "qty"] == 2 and pos.loc["AAA", "strategies"] == "trend_200d" and realized == pytest.approx(2.0)


def test_overselling_and_bad_input_rejected(tmp_path):
    path = tmp_path / "t.csv"
    pf.append_trade(pf.make_trade("BUY", "aaa", 3, 10.0, "2026-01-02"), path)
    with pytest.raises(ValueError, match="only 3"):
        pf.append_trade(pf.make_trade("SELL", "AAA", 4, 10.0, "2026-01-03"), path)
    with pytest.raises(ValueError):
        pf.make_trade("BUY", "AAA", -1, 10.0)
    with pytest.raises(ValueError, match="unknown strategy"):
        pf.make_trade("BUY", "AAA", 1, 10.0, strategy="yolo")
    assert len(pf.load_ledger(path)) == 1 and pf.load_ledger(path).symbol.iloc[0] == "AAA"
    assert pf.undo_last(path)["symbol"] == "AAA" and len(pf.load_ledger(path)) == 0


def test_empty_ledger(tmp_path):
    pos, realized = pf.positions(pf.load_ledger(tmp_path / "missing.csv"))
    assert len(pos) == 0 and realized == 0
