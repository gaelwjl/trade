"""Backtest analytics beyond one stats row: sub-periods, calendar years, rolling windows, cost sensitivity."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..data.prices import Panel
from ..types import StatsTable
from .engine import CostModel, Result
from .metrics import TRADING_DAYS
from .runner import run_suite

STAT_KEYS = ["CAGR", "Vol", "Sharpe", "Sortino", "MaxDD", "Calmar", "Beta", "Alpha", "Turnover/yr", "CostDrag/yr", "AvgExposure"]


@dataclass(frozen=True)
class Period:
    key: str
    label: str
    start: str | None  # inclusive
    end: str | None  # exclusive


def standard_periods(first: pd.Timestamp, last: pd.Timestamp, split: str) -> list[Period]:
    """Full history, in-sample (before ``split``), out-of-sample (from ``split``) and the last five years."""
    s = pd.Timestamp(split)
    five = (last - pd.DateOffset(years=5)).normalize()
    return [
        Period("full", f"Full {first.year}–{last.year}", None, None),
        Period("is", f"In-sample {first.year}–{s.year - 1}", None, split),
        Period("oos", f"Out-of-sample {s.year}–{last.year}", split, None),
        Period("5y", "Last 5 years", str(five.date()), None),
    ]


def period_stats(results: dict[str, Result], period: Period, benchmark: str = "QQQ") -> StatsTable:
    """One row per series, ``STAT_KEYS`` columns, computed on the period only (betas vs ``benchmark``)."""
    cut = {k: r.between(period.start, period.end) for k, r in results.items()}
    bench = cut[benchmark].returns
    rows = {k: {s: v for s, v in r.stats(bench).items() if s in STAT_KEYS} for k, r in cut.items()}
    return StatsTable(pd.DataFrame(rows).T.reindex(columns=STAT_KEYS))


def annual_returns(returns: pd.Series) -> pd.Series:
    """Compounded return per calendar year (the last year may be partial)."""
    return (1 + returns).groupby(pd.DatetimeIndex(returns.index).year).prod() - 1


def rolling_return(result: Result, window: int = TRADING_DAYS) -> pd.Series:
    eq = result.equity
    return eq / eq.shift(window) - 1


def rolling_sharpe(result: Result, window: int = TRADING_DAYS) -> pd.Series:
    roll = result.returns.rolling(window, min_periods=window)
    return roll.mean() / roll.std() * np.sqrt(TRADING_DAYS)


def cost_sensitivity(panel: Panel, levels_bps: list[float], base: CostModel, base_results: dict[str, Result],
                     start: str | None, execution: str) -> dict[float, StatsTable]:
    """Full-period CAGR and Sharpe of the whole suite at each one-way cost level (bps)."""
    out = {}
    for bps in levels_bps:
        alt = base_results if math.isclose(bps, base.one_way * 1e4) else run_suite(
            panel, start=start, costs=CostModel(commission_bps=0.0, slippage_bps=bps, borrow_bps=base.borrow_bps),
            execution=execution)
        bench = alt["QQQ"].returns
        out[bps] = StatsTable(pd.DataFrame({k: {s: r.stats(bench).get(s) for s in ("CAGR", "Sharpe")} for k, r in alt.items()}).T)
    return out
