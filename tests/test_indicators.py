import numpy as np
import pandas as pd
import pytest

from tradelab.strategies import indicators as ind
from tradelab.types import PriceFrame


def frame(values) -> PriceFrame:
    return PriceFrame(pd.DataFrame({"A": values}, index=pd.bdate_range("2024-01-01", periods=len(values))))


def test_rsi_extremes_and_flat():
    assert ind.rsi(frame(np.arange(1.0, 40.0)), 14)["A"].iloc[-1] == pytest.approx(100.0)
    assert ind.rsi(frame(np.arange(40.0, 1.0, -1)), 14)["A"].iloc[-1] == pytest.approx(0.0)
    assert np.isnan(ind.rsi(frame(np.full(40, 5.0)), 14)["A"].iloc[-1])


def test_rsi_matches_wilder_recursion():
    x = 100 + np.cumsum(np.random.default_rng(0).normal(0, 1, 120))
    got = ind.rsi(frame(x), 14)["A"]
    d = np.diff(x)
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    au, ad = up[0], dn[0]  # pandas ewm(adjust=False) seeds with the first observation
    for i in range(1, len(d)):
        au = au + (up[i] - au) / 14
        ad = ad + (dn[i] - ad) / 14
    assert got.iloc[-1] == pytest.approx(100 - 100 / (1 + au / ad))


def test_macd_and_bollinger_identities():
    c = frame(100 + np.cumsum(np.random.default_rng(1).normal(0, 1, 200)))
    line, sig, hist = ind.macd(c)
    assert np.allclose((line - sig).dropna(), hist.dropna())
    up, mid, lo, pctb = ind.bollinger(c)
    ok = up.notna()
    assert ((up >= mid) & (mid >= lo))[ok].all().all()
    assert np.allclose(((c - lo) / (up - lo))[ok], pctb[ok], equal_nan=True)


def test_atr_of_constant_range_bars():
    c = frame(np.full(60, 100.0))
    a = ind.atr(PriceFrame(c + 1), PriceFrame(c - 1), c, 14)
    assert a["A"].iloc[-1] == pytest.approx(2.0)
