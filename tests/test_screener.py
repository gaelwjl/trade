import numpy as np
import pytest

from tradelab.live.screener import screen
from tests.helpers import make_panel


def test_screen_fields_and_stale_symbols_dropped():
    p = make_panel(n_days=400, n_names=5)
    p.close.iloc[-1, 0] = np.nan  # S0 has no bar on the last day -> excluded
    f = screen(p)
    assert "S0" not in f.index and len(f) == 4
    c = p.close["S1"]
    assert f.loc["S1", "chg_1d"] == pytest.approx(c.iloc[-1] / c.iloc[-2] - 1)
    assert f.loc["S1", "from_high"] <= 0


def test_screen_handles_short_history():
    p = make_panel(n_days=120, n_names=3)
    f = screen(p)
    assert len(f) == 3 and f["mom_12_1"].isna().all() and f["chg_1m"].notna().all()
