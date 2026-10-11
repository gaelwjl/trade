"""Performance statistics from a daily return series."""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def drawdown(returns: pd.Series) -> pd.Series:
    eq = (1 + returns.fillna(0)).cumprod()
    return eq / eq.cummax() - 1


def summarize(
    returns: pd.Series, benchmark: pd.Series | None = None, rf: float = 0.0
) -> dict[str, float]:
    """Annualised stats. ``rf`` is an annual risk-free rate used for Sharpe/Sortino."""
    r = returns.dropna()
    if len(r) < 2:
        return {}
    years = len(r) / TRADING_DAYS
    total = float(np.prod(1 + r.to_numpy()))
    cagr = total ** (1 / years) - 1 if total > 0 else -1.0
    vol = r.std() * np.sqrt(TRADING_DAYS)
    excess = r - rf / TRADING_DAYS
    downside = np.sqrt((np.minimum(excess, 0) ** 2).mean()) * np.sqrt(TRADING_DAYS)
    mdd = drawdown(r).min()
    out = {
        "CAGR": cagr,
        "Vol": vol,
        "Sharpe": excess.mean() * TRADING_DAYS / vol if vol > 0 else np.nan,
        "Sortino": excess.mean() * TRADING_DAYS / downside if downside > 0 else np.nan,
        "MaxDD": mdd,
        "Calmar": cagr / abs(mdd) if mdd < 0 else np.nan,
        "Skew": r.skew(),
    }
    if benchmark is not None:
        b = benchmark.reindex(r.index).dropna()
        rr = r.reindex(b.index)
        if len(b) > 2 and b.var() > 0:
            beta = rr.cov(b) / b.var()
            out["Beta"] = beta
            out["Alpha"] = (rr.mean() - beta * b.mean()) * TRADING_DAYS
            out["Corr"] = rr.corr(b)
    return out
