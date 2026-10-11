import numpy as np
import pandas as pd
import pytest

from tradelab.data.prices import Panel, drop_incomplete_bar, one_per_company, short_name
from tradelab.types import Bars, ConstituentTable, PriceFrame

ET = "America/New_York"


def bars(last_day: str) -> Bars:
    idx = pd.bdate_range(end=last_day, periods=3)
    return Bars(pd.DataFrame({"Close": [1.0, 2.0, 3.0]}, index=idx))


@pytest.mark.parametrize(
    "now, kept",
    [
        ("2026-10-09 11:00", 2),  # mid-session: today's live bar is dropped
        ("2026-10-09 16:30", 3),  # after the close: complete
        ("2026-10-10 09:00", 3),  # next day: yesterday's bar is complete
    ],
)
def test_drop_incomplete_bar(now, kept):
    assert (
        len(drop_incomplete_bar(bars("2026-10-09"), pd.Timestamp(now, tz=ET))) == kept
    )


def test_one_per_company_keeps_larger_share_class():
    cons = ConstituentTable(
        pd.DataFrame(
            {
                "symbol": ["GOOG", "GOOGL", "AAPL"],
                "name": [
                    "Alphabet Inc. Class C Capital Stock",
                    "Alphabet Inc. Class A Common Stock",
                    "Apple Inc. Common Stock",
                ],
                "market_cap": [4.25e12, 4.30e12, 4.9e12],
            }
        )
    )
    assert one_per_company(cons) == ["AAPL", "GOOGL"]
    assert short_name("Alphabet Inc. Class C Capital Stock") == "Alphabet Inc."


def test_short_gaps_filled_long_gaps_and_prelisting_left_alone():
    idx = pd.bdate_range("2024-01-01", periods=20)
    a = np.arange(1.0, 21.0)
    a[5] = np.nan  # 1-bar gap -> filled
    a[10:17] = np.nan  # 7-bar gap -> longer than max_gap, left NaN entirely
    b = np.r_[
        [np.nan] * 4, np.arange(1.0, 17.0)
    ]  # listed on day 4 -> pre-listing stays NaN
    close = pd.DataFrame({"A": a, "B": b}, index=idx)
    frames = {k: PriceFrame(close.copy()) for k in ("Open", "High", "Low", "Close")}
    frames["Volume"] = PriceFrame(close.notna() * 1e6)
    with pytest.warns(UserWarning, match="forward-filled 1"):
        p = Panel.from_frames(frames, max_gap=5)
    assert (
        p.close["A"].iloc[5] == 5.0
        and p.open["A"].iloc[5] == 5.0
        and p.volume["A"].iloc[5] == 0
    )
    assert p.close["A"].iloc[10:17].isna().all()
    assert p.close["B"].iloc[:4].isna().all()
    assert p.filled is not None and int(p.filled.to_numpy().sum()) == 1


def test_save_prices_keeps_a_longer_cached_history(tmp_path, monkeypatch):
    from tradelab.data import prices

    monkeypatch.setattr(prices, "PRICES_DIR", tmp_path)
    full = Bars(
        pd.DataFrame({"Close": 1.0}, index=pd.bdate_range("2010-01-01", periods=500))
    )
    prices.save_prices({"OLD": full, "NEW": full})
    stub = Bars(full.iloc[-1:])  # Yahoo purged a delisted company: one bar left
    longer = Bars(
        pd.DataFrame({"Close": 2.0}, index=pd.bdate_range("2009-01-01", periods=800))
    )
    assert prices.save_prices({"OLD": stub, "NEW": longer}) == ["OLD"]
    assert len(pd.read_parquet(prices.price_path("OLD"))) == 500
    assert len(pd.read_parquet(prices.price_path("NEW"))) == 800
