"""Strategy functions and the scores they rank or threshold on.

A strategy is ``fn(panel, **params) -> Weights`` (date x symbol target weights). Row t may use data
through day t's close only; the engine handles execution delay, warm-up NaNs and rebalance sampling.
Strategies pick only from ``panel.eligible`` (the index members of the day); indicators still use a
stock's full price history, which was public before it joined.
Each strategy's *score* function returns the quantity it decides on, so dashboards can show it
without re-implementing the formula. Score functions accept and ignore the strategy's other params.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.prices import Panel
from ..types import Indicator, Mask, Scores, Weights
from .indicators import rsi, sma


# ---------- portfolio construction helpers ----------
def in_universe(p: Panel, score: Scores) -> Scores:
    """``score`` with names outside the universe of each day blanked out."""
    return Scores(score.where(p.eligible))


def equal_weight(mask: Mask, floor: int = 1) -> Weights:
    """Equal weights over True cells, summing to 1. Rows with fewer than ``floor`` True cells are flat."""
    m = mask.fillna(False).astype(float)
    n = m.sum(axis=1)
    return Weights(m.div(n.where(n >= floor), axis=0).fillna(0.0))


def pick(score: Scores, frac: float, top: bool = True, min_names: int = 20) -> Weights:
    """Equal-weight the top (or bottom) ``frac`` of names by ``score``.

    Flat on days when fewer than ``min_names`` names have a score (thin cross-sections make the
    quantile meaningless). The floor applies to the scored universe, not to the selection.
    """
    rank = score.rank(axis=1, pct=True)
    mask = Mask(rank > 1 - frac if top else rank <= frac)
    w = equal_weight(mask)
    return Weights(w.where(score.notna().sum(axis=1) >= min_names, 0.0))


# ---------- scores ----------
def momentum_score(
    p: Panel, lookback: int = 252, skip: int = 21, **_: object
) -> Scores:
    """Trailing return from ``lookback`` to ``skip`` bars ago (12-1 momentum by default)."""
    return Scores(p.close.shift(skip) / p.close.shift(lookback) - 1)


def trend_score(p: Panel, window: int = 200, **_: object) -> Indicator:
    """Distance of the close from its SMA (positive = above)."""
    return Indicator(p.close / sma(p.close, window) - 1)


def volatility_score(p: Panel, window: int = 60, **_: object) -> Scores:
    """Annualised realised volatility of daily returns."""
    return Scores(p.returns.rolling(window, min_periods=window).std() * np.sqrt(252))


def rsi_score(p: Panel, rsi_n: int = 2, **_: object) -> Indicator:
    return rsi(p.close, rsi_n)


def reversal_score(p: Panel, lookback: int = 5, **_: object) -> Scores:
    """Return over the last ``lookback`` bars."""
    return Scores(p.close / p.close.shift(lookback) - 1)


def crossover_score(
    p: Panel, fast: int = 50, slow: int = 200, **_: object
) -> Indicator:
    """Fast SMA relative to slow SMA (positive = fast above slow)."""
    return Indicator(sma(p.close, fast) / sma(p.close, slow) - 1)


# ---------- strategies ----------
def momentum(
    p: Panel, lookback: int = 252, skip: int = 21, top: float = 0.2, min_names: int = 20
) -> Weights:
    """Cross-sectional 12-1 momentum: long the top fraction by trailing return, skipping the last month."""
    score = in_universe(p, momentum_score(p, lookback, skip))
    return pick(score, top, top=True, min_names=min_names)


def trend(p: Panel, window: int = 200) -> Weights:
    """Each stock held (1/N of available names) while above its own SMA; rest in cash."""
    above = (trend_score(p, window) > 0) & p.eligible
    avail = p.eligible.sum(axis=1)
    return Weights(above.astype(float).div(avail, axis=0).fillna(0.0))


def low_vol(
    p: Panel, window: int = 60, bottom: float = 0.2, min_names: int = 20
) -> Weights:
    """Long the lowest-realised-volatility fraction of the universe."""
    score = in_universe(p, volatility_score(p, window))
    return pick(score, bottom, top=False, min_names=min_names)


def rsi_reversion(
    p: Panel,
    rsi_n: int = 2,
    entry: float = 10,
    exit_: float = 65,
    trend_window: int = 200,
    max_positions: int = 10,
    max_hold: int = 10,
) -> Weights:
    """Buy short-term oversold dips in long-term uptrends; fixed slots of 1/max_positions each.

    Entry: RSI < ``entry`` while close > SMA(``trend_window``). When more names qualify than there
    are free slots, the most oversold (lowest RSI) are taken and the rest ignored, so existing
    positions are never resized. Exit on the first of: RSI > ``exit_`` (the bounce), close falls
    below the trend SMA (the setup is broken), ``max_hold`` bars held (time stop), or the stock
    leaving the universe.
    """
    r = rsi_score(p, rsi_n).to_numpy()
    up = (trend_score(p, trend_window) > 0).to_numpy()
    ok = p.eligible.to_numpy()
    n, m = r.shape
    held = np.zeros(m, dtype=bool)
    age = np.zeros(m, dtype=int)
    out = np.zeros((n, m))
    with np.errstate(invalid="ignore"):
        for t in range(n):
            age[held] += 1
            exiting = held & ((r[t] > exit_) | ~up[t] | (age >= max_hold) | ~ok[t])
            held &= ~exiting
            free = max_positions - int(held.sum())
            if (
                free > 0
            ):  # a name exiting on this bar may not re-enter on it (else the time stop never fires)
                cand = np.flatnonzero(~held & ~exiting & ok[t] & up[t] & (r[t] < entry))
                new = cand[np.argsort(r[t, cand], kind="stable")[:free]]
                held[new], age[new] = True, 0
            out[t] = held / max_positions
    return Weights(pd.DataFrame(out, index=p.close.index, columns=p.close.columns))


def reversal_weekly(
    p: Panel, lookback: int = 5, bottom: float = 0.2, min_names: int = 20
) -> Weights:
    """Cross-sectional short-term reversal: long last week's biggest losers."""
    score = in_universe(p, reversal_score(p, lookback))
    return pick(score, bottom, top=False, min_names=min_names)


def index_trend(p: Panel, fast: int = 50, slow: int = 200) -> Weights:
    """Single-asset trend filter (run on a panel holding one index/ETF, e.g. QQQ): long when fast SMA > slow SMA."""
    return Weights(((crossover_score(p, fast, slow) > 0) & p.eligible).astype(float))


def long_short_momentum(
    p: Panel,
    lookback: int = 252,
    skip: int = 21,
    frac: float = 0.2,
    min_names: int = 20,
) -> Weights:
    """Dollar-neutral momentum: long top fraction, short bottom fraction (0.5 gross each side)."""
    score = in_universe(p, momentum_score(p, lookback, skip))
    longs = pick(score, frac, top=True, min_names=min_names)
    shorts = pick(score, frac, top=False, min_names=min_names)
    return Weights(0.5 * longs - 0.5 * shorts)
