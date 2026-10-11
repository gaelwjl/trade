from tradelab.backtest.runner import sweep
from tradelab.strategies.registry import REGISTRY
from tests.helpers import make_panel


def test_sweep_keeps_failed_combinations():
    p = make_panel(n_days=400, n_names=30)
    out = sweep(p, REGISTRY["momentum_12_1"], {"min_names": [10, 1000]})
    assert len(out) == 2 and "never took a position" in out.loc[out.min_names == 1000, "error"].iloc[0]
