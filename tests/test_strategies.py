import numpy as np
import pandas as pd
import pytest

from tradelab.data.prices import Panel
from tradelab.strategies.library import rsi_reversion
from tradelab.strategies.registry import REGISTRY
from tests.helpers import make_panel

FIELDS = ("open", "high", "low", "close", "volume")


@pytest.mark.parametrize("name", list(REGISTRY))
def test_strategy_and_score_are_causal(name):
    """Weights (and the displayed score) at day t computed on data truncated at t equal those from the full history."""
    p = make_panel(n_days=700, n_names=40, seed=3)
    spec = REGISTRY[name]
    cut = 500
    trunc = Panel(*(getattr(p, f).iloc[:cut] for f in FIELDS))
    pd.testing.assert_frame_equal(
        spec.weights(p).iloc[:cut], spec.weights(trunc), check_freq=False, atol=1e-12
    )
    pd.testing.assert_frame_equal(
        spec.signal(p).iloc[:cut], spec.signal(trunc), check_freq=False, atol=1e-12
    )
    assert (
        spec.weights(p).abs().sum(axis=1).max() > 0
    ), "strategy never trades on synthetic data"


def test_momentum_holds_the_top_quintile_of_its_score():
    p = make_panel(n_days=400, n_names=40, seed=4)
    spec = REGISTRY["momentum_12_1"]
    w, score = spec.weights(p).iloc[-1], spec.signal(p).iloc[-1]
    assert set(w[w > 0].index) == set(score.nlargest(8).index)


def test_rsi_reversion_fixed_slots_and_time_stop():
    p = make_panel(n_days=900, n_names=40, seed=5)
    w = rsi_reversion(p, entry=30, max_positions=5, max_hold=4)
    assert set(np.unique(w.to_numpy().round(12))) <= {
        0.0,
        0.2,
    }  # positions are never resized
    assert (w > 0).sum(axis=1).max() <= 5
    held = (w > 0).to_numpy()
    runs = []
    for j in range(held.shape[1]):
        e = np.diff(np.r_[0, held[:, j].astype(int), 0])
        runs += list(np.flatnonzero(e == -1) - np.flatnonzero(e == 1))
    assert max(runs) <= 4
