import numpy as np
import pandas as pd
import pytest

from tradelab.backtest.engine import CostModel, backtest, rebalance_dates
from tests.helpers import NO_COST, make_panel

def test_buy_and_hold_matches_price_ratio():
    p = make_panel(names=["A"])
    w = pd.DataFrame(1.0, p.close.index, ["A"])
    r = backtest(p, w, rebalance="D", costs=NO_COST)
    # decision at close of day 0 fills at open of day 1; equity marked at opens
    assert r.equity.iloc[-1] == pytest.approx(p.open["A"].iloc[-1] / p.open["A"].iloc[1])


def test_single_entry_costs_exactly_one_way():
    p = make_panel(names=["A"])
    w = pd.DataFrame(1.0, p.close.index, ["A"])
    c = CostModel(commission_bps=2, slippage_bps=3, borrow_bps=0)
    free = backtest(p, w, rebalance="D", costs=NO_COST)
    paid = backtest(p, w, rebalance="D", costs=c)
    assert paid.equity.iloc[-1] / free.equity.iloc[-1] == pytest.approx(1 - 5e-4)
    assert paid.turnover.sum() == pytest.approx(1.0)  # one entry, then daily rebalances trade nothing


def test_weights_drift_between_rebalances():
    p = make_panel(n_days=60, names=["A", "B"])
    w = pd.DataFrame(0.5, p.close.index, ["A", "B"])
    r = backtest(p, w, rebalance=30, costs=NO_COST)
    # after first fill (day 30) no rebalance until day 60 -> weights must drift from 50/50
    wk = r.weights.iloc[-1]
    assert wk.sum() == pytest.approx(1.0)
    assert abs(wk["A"] - 0.5) > 1e-4


def test_last_decision_never_executes_and_future_prices_dont_leak():
    p = make_panel(n_days=300, n_names=5)
    w = pd.DataFrame(0.2, p.close.index, p.close.columns)
    base = backtest(p, w, rebalance="D", costs=NO_COST)
    w2 = w.copy()
    w2.iloc[-1] = 0.0  # decision on the final close has nothing to execute against
    assert backtest(p, w2, rebalance="D", costs=NO_COST).equity.equals(base.equity)

    # equity through day T must not change if prices after T are altered
    T = 200
    p2 = make_panel(n_days=300, n_names=5)
    for f in ("open", "high", "low", "close"):
        getattr(p2, f).iloc[T + 1 :] *= 3.0
    a = backtest(p, w, rebalance="D", costs=NO_COST).equity.iloc[:T]
    b = backtest(p2, w, rebalance="D", costs=NO_COST).equity.iloc[:T]
    assert np.allclose(a, b)


def test_short_borrow_cost_accrues():
    p = make_panel(n_days=260, names=["A"])
    w = pd.DataFrame(-1.0, p.close.index, ["A"])
    plain = backtest(p, w, rebalance="D", costs=NO_COST)
    borrow = backtest(p, w, rebalance="D", costs=CostModel(0, 0, 100))
    assert borrow.equity.iloc[-1] < plain.equity.iloc[-1]


def test_start_makes_book_flat_until_start():
    p = make_panel(n_days=400, n_names=3)
    w = pd.DataFrame(1 / 3, p.close.index, p.close.columns)
    r = backtest(p, w, rebalance="D", costs=NO_COST, start="2016-06-01")
    assert r.equity.index[0] >= pd.Timestamp("2016-05-25")
    assert r.weights.abs().sum(axis=1).iloc[0] == 0


def test_monthly_rebalance_dates_are_last_trading_day():
    idx = pd.bdate_range("2024-01-01", "2024-04-30")
    d = idx[rebalance_dates(idx, "M")]
    assert list(d) == [pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-29"), pd.Timestamp("2024-03-29"), pd.Timestamp("2024-04-30")]


def test_weight_row_t_first_affects_equity_two_bars_later():
    """Row t is decided at close t, fills at open t+1, and first earns open[t+2]/open[t+1]."""
    p = make_panel(n_days=100, n_names=3, seed=7)
    base_w = pd.DataFrame(1 / 3, p.close.index, p.close.columns)
    base = backtest(p, base_w, rebalance="D", costs=NO_COST).equity
    t = 50
    w = base_w.copy()
    w.iloc[t] = [1.0, 0.0, 0.0]
    eq = backtest(p, w, rebalance="D", costs=NO_COST).equity
    d = p.close.index
    assert eq[d[t]] == pytest.approx(base[d[t]]) and eq[d[t + 1]] == pytest.approx(base[d[t + 1]])
    assert eq[d[t + 2]] != pytest.approx(base[d[t + 2]])
    # a perturbed row must also change nothing before it, i.e. earlier equity is identical
    assert np.allclose(eq.loc[: d[t + 1]], base.loc[: d[t + 1]])


def test_weights_on_unknown_symbol_raise():
    p = make_panel(n_days=50, names=["A"])
    w = pd.DataFrame({"A": 0.5, "TYPO": 0.5}, index=p.close.index)
    with pytest.raises(ValueError, match="TYPO"):
        backtest(p, w, rebalance="M")


def test_between_rebases_and_keeps_first_period_return():
    p = make_panel(n_days=300, n_names=3)
    r = backtest(p, pd.DataFrame(1 / 3, p.close.index, p.close.columns), rebalance="M", costs=NO_COST)
    d = r.equity.index
    sub = r.between(d[100], d[200])
    assert sub.equity.iloc[0] == 1.0 and sub.equity.index[0] == d[99]  # bar before start is the base
    assert sub.equity.index[-1] == d[199]  # end is exclusive
    assert sub.equity.iloc[-1] == pytest.approx(r.equity[d[199]] / r.equity[d[99]])
