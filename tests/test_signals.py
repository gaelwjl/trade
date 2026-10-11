import datetime as dt

import pandas as pd
import pytest

from tradelab.live import signals
from tradelab.strategies.registry import REGISTRY
from tests.helpers import make_panel

ET = "America/New_York"


@pytest.mark.parametrize("before, after, act", [
    (0, 0.1, "BUY"), (0.1, 0, "SELL"), (0, -0.1, "SHORT"), (-0.1, 0, "COVER"),
    (0.1, 0.2, "ADD"), (0.2, 0.1, "TRIM"), (0.1, -0.1, "FLIP"), (0.1, 0.1, "HOLD"),
])
def test_action(before, after, act):
    assert signals.action(before, after) == act


@pytest.mark.parametrize("now, expect", [
    ("2026-10-09 15:00", dt.date(2026, 10, 8)),   # Friday, session still open -> Thursday's close is the latest
    ("2026-10-09 17:00", dt.date(2026, 10, 9)),
    ("2026-10-11 10:00", dt.date(2026, 10, 9)),   # Sunday
    ("2026-04-03 18:00", dt.date(2026, 4, 2)),    # Good Friday: no session
])
def test_expected_last_session(now, expect):
    assert signals.expected_last_session(pd.Timestamp(now, tz=ET)) == expect


@pytest.mark.parametrize("name", ["momentum_12_1", "reversal_1w", "rsi2_reversion", "ls_momentum"])
def test_trades_turn_previous_target_into_new_target(name):
    p = make_panel(n_days=500, n_names=40, seed=11)
    prev = None
    for d in signals.decisions(REGISTRY[name], p, 120):
        hold = {h.symbol: h.weight for h in d.holdings}
        if d.is_rebalance and prev is not None:
            rebuilt = dict(prev)
            for t in d.trades:
                rebuilt[t.symbol] = t.after
            assert {k: v for k, v in rebuilt.items() if abs(v) > 1e-9} == pytest.approx(hold)
        elif prev is not None:
            assert hold == prev and not d.trades  # no trading between rebalances
        prev = hold
