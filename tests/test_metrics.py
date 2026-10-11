import numpy as np
import pandas as pd
import pytest

from tradelab.backtest.metrics import drawdown, summarize


def test_constant_return_cagr_and_drawdown():
    r = pd.Series(0.001, index=pd.bdate_range("2020-01-01", periods=252))
    s = summarize(r)
    assert s["CAGR"] == pytest.approx(1.001**252 - 1)
    assert s["MaxDD"] == 0


def test_drawdown_value():
    r = pd.Series([0.1, -0.2, 0.05])
    assert drawdown(r).min() == pytest.approx(-0.2)


def test_beta_of_scaled_benchmark():
    b = pd.Series(np.random.default_rng(1).normal(0, 0.01, 500), index=pd.bdate_range("2020-01-01", periods=500))
    s = summarize(2 * b, b)
    assert s["Beta"] == pytest.approx(2.0)
    assert s["Alpha"] == pytest.approx(0.0, abs=1e-9)
