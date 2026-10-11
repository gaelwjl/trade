"""Snapshot screener: one row per symbol with momentum / trend / volatility / volume fields."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.prices import Panel, load_constituents, short_name
from ..strategies import indicators as ind
from ..types import PriceFrame, ScreenTable


def _ago(c: PriceFrame, k: int) -> pd.Series:
    """Row ``k`` bars before the last one (all NaN when the history is shorter)."""
    return c.iloc[-1 - k] if len(c) > k else pd.Series(np.nan, index=c.columns)


def screen(panel: Panel, history: int = 1000) -> ScreenTable:
    """Fields as of the panel's last bar. Symbols with no price on that bar (stale/halted) are dropped."""
    c, v = panel.close.iloc[-history:], panel.volume.iloc[-history:]
    ret = c.pct_change(fill_method=None)
    s50, s200 = ind.sma(c, 50), ind.sma(c, 200)
    _, _, hist = ind.macd(c)
    _, _, _, pctb = ind.bollinger(c)
    prior = c[pd.DatetimeIndex(c.index).year < c.index[-1].year]
    prev_year_end = prior.iloc[-1] if len(prior) else pd.Series(np.nan, index=c.columns)

    f = pd.DataFrame(
        {
            "price": c.iloc[-1],
            "chg_1d": c.iloc[-1] / _ago(c, 1) - 1,
            "chg_5d": c.iloc[-1] / _ago(c, 5) - 1,
            "chg_1m": c.iloc[-1] / _ago(c, 21) - 1,
            "chg_3m": c.iloc[-1] / _ago(c, 63) - 1,
            "ytd": c.iloc[-1] / prev_year_end - 1,
            "mom_12_1": _ago(c, 21) / _ago(c, 252) - 1,
            "from_high": c.iloc[-1] / c.iloc[-252:].max() - 1,
            "rsi": ind.rsi(c).iloc[-1],
            "vs50": c.iloc[-1] / s50.iloc[-1] - 1,
            "vs200": c.iloc[-1] / s200.iloc[-1] - 1,
            "golden": (s50.iloc[-1] > s200.iloc[-1]).where(s200.iloc[-1].notna()),
            "vol60": ret.iloc[-60:].std() * np.sqrt(252),
            "vol_ratio": v.iloc[-1] / v.iloc[-21:-1].mean(),
            "new_high20": c.iloc[-1] >= c.iloc[-20:].max(),
            "bb_pct": pctb.iloc[-1],
            "macd_hist": hist.iloc[-1],
        }
    )
    f = f[panel.close.iloc[-1].notna()]
    try:
        meta = load_constituents().set_index("symbol")
        f.insert(0, "name", meta["name"].map(short_name).reindex(f.index))
        f.insert(1, "mcap", meta["market_cap"].reindex(f.index))
    except FileNotFoundError:
        pass
    f.index.name = "symbol"
    return ScreenTable(f)


PRESETS = {
    "oversold": lambda f: f[f.rsi < 30].sort_values("rsi"),
    "overbought": lambda f: f[f.rsi > 70].sort_values("rsi", ascending=False),
    "high": lambda f: f[f.from_high > -0.03].sort_values("from_high", ascending=False),
    "golden": lambda f: f[f.golden == True].sort_values("vs200", ascending=False),  # noqa: E712
    "volume": lambda f: f[f.vol_ratio > 2].sort_values("vol_ratio", ascending=False),
}
