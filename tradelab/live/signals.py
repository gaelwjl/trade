"""Inference only: for each strategy and recent session, the target portfolio in force after that close
and, on rebalance days, the trades versus the previous target (filled at the next session's open).

No backtest is run. Weight drift since the last rebalance is ignored: rebalancing to target also trims
drift. This is the mechanical output of the strategies, not investment advice.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from ..backtest.engine import rebalance_dates
from ..data import market_calendar as cal
from ..data.prices import MARKET_TZ, SESSION_DONE, Panel
from ..strategies.registry import REGISTRY, StrategySpec

EPS = 1e-9
Action = str  # BUY | SELL | SHORT | COVER | ADD | TRIM | FLIP | HOLD


@dataclass(frozen=True)
class Position:
    symbol: str
    weight: float
    signal: float  # the strategy's score for this name on the decision's date
    price: float  # close on the decision's date


@dataclass(frozen=True)
class Trade:
    symbol: str
    action: Action
    before: float
    after: float
    signal: float
    price: float


@dataclass(frozen=True)
class Decision:
    date: dt.date
    is_rebalance: bool  # does this close produce orders for the next open?
    target_since: dt.date | None  # close at which the target in force was set
    next_rebalance: dt.date
    holdings: tuple[Position, ...]  # target in force after this close
    trades: tuple[Trade, ...]  # empty unless ``is_rebalance``


def action(before: float, after: float) -> Action:
    if abs(after - before) < EPS:
        return "HOLD"
    if abs(before) < EPS:
        return "BUY" if after > 0 else "SHORT"
    if abs(after) < EPS:
        return "SELL" if before > 0 else "COVER"
    if (before > 0) != (after > 0):
        return "FLIP"
    return "ADD" if abs(after) > abs(before) else "TRIM"


def expected_last_session(now: pd.Timestamp | None = None) -> dt.date:
    """The most recent session whose close has happened (what fresh data should end on)."""
    now = now if now is not None else pd.Timestamp.now(tz=MARKET_TZ)
    d = now.date()
    return (
        d if cal.is_session(d) and now.time() >= SESSION_DONE else cal.prev_session(d)
    )


def decisions(spec: StrategySpec, panel: Panel, days: int) -> list[Decision]:
    """One ``Decision`` per session for the last ``days`` sessions of ``panel``."""
    w = spec.weights(panel)
    idx = pd.DatetimeIndex(w.index)
    decide = rebalance_dates(idx, spec.rebalance)
    W, px, F = w.to_numpy(), panel.close.to_numpy(), spec.signal(panel).to_numpy()
    syms = list(w.columns)
    dec_pos = np.flatnonzero(decide)
    last = idx[-1].date()
    after_data = (
        cal.next_session(last)
        if spec.rebalance == "D"
        else cal.next_period_end(last, spec.rebalance)
    )

    out = []
    for i in range(max(len(idx) - days, 0), len(idx)):
        past, future = dec_pos[dec_pos <= i], dec_pos[dec_pos > i]
        cur = past[-1] if len(past) else None
        holdings = (
            ()
            if cur is None
            else tuple(
                Position(syms[j], float(W[cur, j]), float(F[i, j]), float(px[i, j]))
                for j in np.flatnonzero(np.abs(W[cur]) > EPS)
            )
        )
        trades: tuple[Trade, ...] = ()
        if decide[i]:
            before = W[past[-2]] if len(past) > 1 else np.zeros(len(syms))
            trades = tuple(
                Trade(
                    syms[j],
                    action(before[j], W[i, j]),
                    float(before[j]),
                    float(W[i, j]),
                    float(F[i, j]),
                    float(px[i, j]),
                )
                for j in np.flatnonzero(np.abs(W[i] - before) > EPS)
            )
        out.append(
            Decision(
                date=idx[i].date(),
                is_rebalance=bool(decide[i]),
                target_since=idx[cur].date() if cur is not None else None,
                next_rebalance=idx[future[0]].date() if len(future) else after_data,
                holdings=holdings,
                trades=trades,
            )
        )
    return out


def load_panels(specs: Iterable[StrategySpec]) -> dict[str, Panel]:
    panels: dict[str, Panel] = {}
    for s in specs:
        if s.universe not in panels:
            panels[s.universe] = Panel.load_universe(s.universe)
    return panels


def latest(
    days: int = 63, panels: dict[str, Panel] | None = None
) -> dict[str, list[Decision]]:
    """Decisions for every registered strategy over the last ``days`` sessions."""
    panels = panels or load_panels(REGISTRY.values())
    return {
        name: decisions(spec, panels[spec.universe], days)
        for name, spec in REGISTRY.items()
    }
