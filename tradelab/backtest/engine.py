"""Daily-bar portfolio backtest engine (target weights in, equity curve out).

Timing convention -- no lookahead by construction:
  * A strategy produces target weights for every date using data up to and INCLUDING that
    day's close (a decision made at the close of day t-1).
  * On rebalance days the decision executes at the next bar's price: ``execution="next_open"``
    (default) fills at day t's open, ``"next_close"`` at day t's close.
  * Between rebalances positions drift with prices (no daily re-balancing).
  * Equity is marked at the execution price series (open or close), so returns are
    open-to-open (or close-to-close) with the position changes landing exactly on those marks.

Costs: one-way ``(commission_bps + slippage_bps)`` on traded notional, ``borrow_bps`` per year
on short notional, and ``cash_rate`` per year earned on uninvested cash (negative cash = leverage
financed at the same rate). Defaults are sized for liquid large caps.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data import market_calendar as cal
from ..data.prices import Panel
from ..types import Weights
from .metrics import TRADING_DAYS, summarize


@dataclass(frozen=True)
class CostModel:
    commission_bps: float = 1.0
    slippage_bps: float = 4.0
    borrow_bps: float = 50.0  # annual, on short notional

    @property
    def one_way(self) -> float:
        return (self.commission_bps + self.slippage_bps) / 1e4


@dataclass
class Result:
    equity: pd.Series
    weights: Weights  # weights actually held after each bar's rebalance
    turnover: pd.Series  # sum |trade weights| per bar
    costs: pd.Series  # fraction of equity paid in trading costs per bar
    name: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def returns(self) -> pd.Series:
        return self.equity.pct_change().dropna()

    @property
    def drawdown(self) -> pd.Series:
        return self.equity / self.equity.cummax() - 1

    @property
    def exposure(self) -> pd.Series:
        return self.weights.abs().sum(axis=1)

    def between(self, start: str | pd.Timestamp | None = None, end: str | pd.Timestamp | None = None) -> "Result":
        """Sub-period [start, end) rebased to 1. The bar before ``start`` is kept as the base so the
        first in-period return is not lost."""
        idx = self.equity.index
        lo = max(int(idx.searchsorted(pd.Timestamp(start))) - 1, 0) if start is not None else 0
        hi = int(idx.searchsorted(pd.Timestamp(end))) if end is not None else len(idx)
        eq = self.equity.iloc[lo:hi]
        return Result(
            equity=eq / eq.iloc[0], weights=Weights(self.weights.iloc[lo:hi]), turnover=self.turnover.iloc[lo:hi],
            costs=self.costs.iloc[lo:hi], name=self.name, meta=self.meta,
        )

    def stats(self, benchmark: pd.Series | None = None, rf: float = 0.0) -> dict[str, float]:
        s = summarize(self.returns, benchmark, rf)
        if s:
            s["Turnover/yr"] = self.turnover.sum() / (len(self.turnover) / TRADING_DAYS)
            s["AvgExposure"] = self.exposure.mean()
            s["CostDrag/yr"] = self.costs.sum() / (len(self.costs) / TRADING_DAYS)
        return s


def rebalance_dates(index: pd.DatetimeIndex, rebalance: str | int) -> np.ndarray:
    """Boolean mask of decision days: the last trading day of each period.

    ``rebalance``: "D" daily, "W"/"W-FRI" weekly, "M" monthly, "Q" quarterly, or an int N for
    every N trading days.
    """
    if isinstance(rebalance, int):
        mask = np.zeros(len(index), dtype=bool)
        mask[rebalance - 1 :: rebalance] = True
        return mask
    rule = {"D": None, "W": "W-FRI", "M": "ME", "Q": "QE"}.get(rebalance, rebalance)
    if rule is None:
        return np.ones(len(index), dtype=bool)
    last = pd.Series(index, index=index).resample(rule).last().dropna()
    mask = index.isin(last.values)
    # The final period may still be running: its last *available* day is not a decision day
    # unless the calendar says it really is the period's last session.
    if len(index) and mask[-1] and rule in ("W-FRI", "ME", "QE") and not cal.is_period_end(index[-1].date(), rule):
        mask[-1] = False
    return mask


def backtest(
    panel: Panel,
    weights: Weights,
    rebalance: str | int = "M",
    execution: str = "next_open",
    costs: CostModel = CostModel(),
    cash_rate: float = 0.0,
    start: str | None = None,
    end: str | None = None,
    name: str = "",
) -> Result:
    """Run a backtest.

    ``weights``: date x symbol target weights (fraction of equity; negative = short; NaN = 0).
    Sampled on rebalance dates; each row may use information through that date's close.
    """
    if execution not in ("next_open", "next_close"):
        raise ValueError("execution must be 'next_open' or 'next_close'")
    px = (panel.open if execution == "next_open" else panel.close).copy()
    idx = px.index
    cols = px.columns
    unknown = [s for s in weights.columns if s not in cols and weights[s].fillna(0).ne(0).any()]
    if unknown:
        raise ValueError(f"weights on symbols missing from the panel: {unknown[:10]}")
    W = weights.reindex(index=idx, columns=cols).fillna(0.0).to_numpy(float, copy=True)
    if start:  # strategy may use pre-start data as warm-up, but the book starts flat at `start`
        W[idx < pd.Timestamp(start)] = 0.0

    valid = px.notna().to_numpy()
    X = px.ffill().to_numpy(float)  # stale-forward so a missing day is a 0% return, not a crash
    with np.errstate(invalid="ignore", divide="ignore"):
        R = X[1:] / X[:-1] - 1.0
    R = np.nan_to_num(R, nan=0.0, posinf=0.0, neginf=0.0)

    decide = rebalance_dates(pd.DatetimeIndex(idx), rebalance)
    n, m = X.shape
    c1, borrow_d, cash_d = costs.one_way, costs.borrow_bps / 1e4 / TRADING_DAYS, cash_rate / TRADING_DAYS

    equity = np.ones(n)
    held = np.zeros((n, m))
    turnover = np.zeros(n)
    cost_frac = np.zeros(n)
    w = np.zeros(m)
    eq = 1.0
    for t in range(1, n):
        # mark: positions carried from the previous bar
        ret = R[t - 1]
        cash_w = 1.0 - w.sum()
        short_w = -w[w < 0].sum()
        pr = float(w @ ret) + cash_w * cash_d - short_w * borrow_d
        eq *= 1.0 + pr
        if abs(1.0 + pr) > 1e-12:
            w = w * (1.0 + ret) / (1.0 + pr)
        # trade: decision made at the previous close executes now
        if decide[t - 1]:
            tgt = np.where(valid[t], W[t - 1], 0.0)
            trade = np.abs(tgt - w).sum()
            eq *= 1.0 - trade * c1
            turnover[t], cost_frac[t] = trade, trade * c1
            w = tgt
        equity[t], held[t] = eq, w

    active = np.flatnonzero(np.abs(held).sum(axis=1) > 0)
    if len(active) == 0:
        raise ValueError("Strategy never took a position")
    first = max(active[0] - 1, 0)  # include the bar before the first trade as the base
    sl = slice(first, None)
    res = Result(
        equity=pd.Series(equity, idx)[sl] / equity[first],
        weights=Weights(pd.DataFrame(held, idx, cols)[sl]),
        turnover=pd.Series(turnover, idx)[sl],
        costs=pd.Series(cost_frac, idx)[sl],
        name=name,
        meta={"rebalance": rebalance, "execution": execution, "costs": costs, "cash_rate": cash_rate},
    )
    return res.between(None, pd.Timestamp(end) + pd.Timedelta(days=1)) if end else res


def buy_and_hold(close: pd.Series, name: str = "") -> pd.Series:
    """Close-to-close daily returns of holding one asset (benchmark helper)."""
    return close.pct_change().dropna().rename(name)
