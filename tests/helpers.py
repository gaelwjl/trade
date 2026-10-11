"""Shared synthetic data for tests."""

import numpy as np
import pandas as pd

from tradelab.backtest.engine import CostModel
from tradelab.data.prices import Panel

NO_COST = CostModel(0, 0, 0)


def make_panel(n_days=700, n_names=40, seed=0, names=None) -> Panel:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2015-01-01", periods=n_days)
    cols = names or [f"S{i}" for i in range(n_names)]
    close = pd.DataFrame(
        100 * np.exp(np.cumsum(rng.normal(3e-4, 0.015, (n_days, len(cols))), axis=0)),
        idx,
        cols,
    )
    open_ = close.shift(1) * (1 + rng.normal(0, 0.003, close.shape))
    open_.iloc[0] = close.iloc[0]
    vol = pd.DataFrame(1e6, idx, cols)
    return Panel(open_, close * 1.01, close * 0.99, close, vol)
