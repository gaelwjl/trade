import datetime as dt

import pandas as pd

from tradelab.backtest.engine import rebalance_dates
from tradelab.data import market_calendar as cal

D = dt.date


def test_2026_holidays():
    assert cal.nyse_holidays(2026) == {
        D(2026, 1, 1),
        D(2026, 1, 19),
        D(2026, 2, 16),
        D(2026, 4, 3),
        D(2026, 5, 25),
        D(2026, 6, 19),
        D(2026, 7, 3),
        D(2026, 9, 7),
        D(2026, 11, 26),
        D(2026, 12, 25),
    }


def test_saturday_new_year_not_observed():
    assert D(2021, 12, 31) not in cal.nyse_holidays(2021) and D(
        2022, 1, 1
    ) not in cal.nyse_holidays(2022)


def test_next_session_and_period_ends():
    assert cal.next_session(D(2026, 10, 9)) == D(
        2026, 10, 12
    )  # Fri -> Mon (Columbus Day is a trading day)
    assert cal.next_session(D(2026, 4, 2)) == D(2026, 4, 6)  # Good Friday skipped
    assert cal.is_period_end(
        D(2026, 4, 2), "W"
    )  # Thursday before Good Friday ends the week
    assert not cal.is_period_end(D(2026, 10, 9), "M")
    assert cal.is_period_end(D(2026, 10, 30), "M")
    assert cal.next_period_end(D(2026, 10, 9), "M") == D(2026, 10, 30)
    assert cal.next_period_end(D(2026, 10, 9), "W") == D(2026, 10, 16)


def test_running_period_is_not_a_decision_day():
    mid = pd.bdate_range("2026-09-01", "2026-10-07")  # ends on a Wednesday, mid-month
    m = rebalance_dates(mid, "M")
    assert not m[-1] and mid[m][-1] == pd.Timestamp("2026-09-30")
    assert not rebalance_dates(mid, "W")[-1]
    done = pd.bdate_range("2026-09-01", "2026-10-30")
    assert rebalance_dates(done, "M")[-1] and rebalance_dates(done, "W")[-1]
