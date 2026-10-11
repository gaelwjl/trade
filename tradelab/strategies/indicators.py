"""Technical indicators on wide (date x symbol) price frames. All are causal: the value at t uses
data through t only."""

from __future__ import annotations

import numpy as np

from ..types import Indicator, PriceFrame


def sma(x: PriceFrame, n: int) -> Indicator:
    return Indicator(x.rolling(n, min_periods=n).mean())


def ema(x: PriceFrame | Indicator, n: int) -> Indicator:
    return Indicator(x.ewm(span=n, adjust=False, min_periods=n).mean())


def rsi(close: PriceFrame, n: int = 14) -> Indicator:
    """Wilder RSI. 100 when there were no down moves in the window, NaN when completely flat."""
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100 - 100 / (1 + up / dn)
    return Indicator(out.where(~((up == 0) & (dn == 0))))


def macd(
    close: PriceFrame, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[Indicator, Indicator, Indicator]:
    """Returns (macd line, signal line, histogram)."""
    line = Indicator(ema(close, fast) - ema(close, slow))
    sig = ema(line, signal)
    return line, sig, Indicator(line - sig)


def bollinger(
    close: PriceFrame, n: int = 20, k: float = 2.0
) -> tuple[Indicator, Indicator, Indicator, Indicator]:
    """Returns (upper, middle, lower, %B)."""
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    up, lo = mid + k * sd, mid - k * sd
    return Indicator(up), mid, Indicator(lo), Indicator((close - lo) / (up - lo))
