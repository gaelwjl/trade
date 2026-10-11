import datetime as dt

import pandas as pd
import pytest

from tradelab.data import membership as mb
from tradelab.types import MembershipTable

T = pd.Timestamp

Y2021 = """
year: 2021
tickers_on_Jan_1:
  - AAPL
  - FB
  - ON
  - GOOG
changes:
  '2021-06-21':
    difference:
      - ON
    union:
      - MNST
"""
Y2022 = """
year: 2022
tickers_on_Jan_1:
changes:
  '2022-06-09':
    difference:
      - FB
    union:
      - META
  '2022-07-01':
    union:
      - GOOGL
"""


def spells(rows) -> MembershipTable:
    """Spells as ``parse_changes`` returns them (symbol = ticker)."""
    df = pd.DataFrame(rows, columns=["ticker", "start", "end"])
    return MembershipTable(df.assign(symbol=df["ticker"])[mb.COLUMNS])


def test_parse_changes_builds_spells_and_keeps_tickers_as_strings():
    s = mb.parse_changes([Y2021, Y2022])
    on = s[s.ticker == "ON"].iloc[0]  # YAML 1.1 would read ON as True
    assert (on.start, on.end) == (T("2021-01-01"), T("2021-06-21"))
    fb = s[s.ticker == "FB"].iloc[0]
    assert fb.end == T("2022-06-09")
    assert s[s.ticker == "META"].iloc[0].start == T("2022-06-09")
    assert pd.isna(s[s.ticker == "AAPL"].iloc[0].end)


def test_parse_changes_rejects_inconsistent_history():
    bad = Y2021.replace("- ON\n    union", "- XYZ\n    union")
    with pytest.raises(ValueError, match="XYZ leaves the index but is not a member"):
        mb.parse_changes([bad])


def test_rename_is_one_continuous_spell():
    s = mb.resolve(mb.parse_changes([Y2021, Y2022]))
    meta = s[s.symbol == "META"]
    assert len(meta) == 1 and meta.iloc[0].start == T("2021-01-01")
    assert pd.isna(meta.iloc[0].end)


def test_reused_ticker_before_cutoff_has_no_symbol():
    # MNST before 2012-01-09 was Monster Worldwide, not today's Monster Beverage
    s = mb.resolve(
        spells(
            [
                ("MNST", T("2007-01-01"), T("2008-11-10")),
                ("HANS", T("2007-01-01"), T("2012-01-09")),
                ("MNST", T("2012-01-09"), pd.NaT),
            ]
        )
    )
    worldwide = s[s.ticker == "MNST"].iloc[0]
    assert worldwide.symbol is None or pd.isna(worldwide.symbol)
    beverage = s[s.symbol == "MNST"]
    assert len(beverage) == 1 and beverage.iloc[0].ticker == "HANS"
    assert beverage.iloc[0].start == T("2007-01-01") and pd.isna(beverage.iloc[0].end)


def test_alias_cutoff_splits_a_spell():
    s = mb.resolve(spells([("FOXA", T("2013-07-01"), T("2021-12-20"))]))
    assert len(s) == 2
    old, new = s.iloc[0], s.iloc[1]
    assert pd.isna(old.symbol) and old.end == T("2019-03-19")
    assert new.symbol == "FOXA" and new.start == T("2019-03-19")


def test_overlapping_spells_of_one_symbol_raise():
    with pytest.raises(ValueError, match="overlapping"):
        mb.resolve(
            spells(
                [
                    ("HANS", T("2010-01-01"), T("2013-01-01")),
                    ("MNST", T("2012-06-01"), pd.NaT),
                ]
            )
        )


def test_reconcile_follows_nasdaqs_live_list():
    s = mb.resolve(mb.parse_changes([Y2021, Y2022]))
    fixed, notes = mb.reconcile(
        s, ["AAPL", "META", "GOOG", "GOOGL", "NEW"], dt.date(2022, 9, 1)
    )
    assert mb.members_on(fixed, T("2022-09-01")) == {
        "AAPL",
        "META",
        "GOOG",
        "GOOGL",
        "NEW",
    }
    assert fixed[fixed.symbol == "MNST"].iloc[0].end == T("2022-09-01")
    assert len(notes) == 2


def test_mark_priced_rejects_histories_that_start_after_the_spell():
    s = mb.resolve(
        spells(
            [
                (
                    "DELL",
                    T("2007-01-01"),
                    T("2013-10-29"),
                ),  # Yahoo DELL starts 2016: new company
                ("AAPL", T("2007-01-01"), pd.NaT),
                ("KHC", T("2015-07-02"), pd.NaT),  # first bar 4 days later: fine
                ("GONE", T("2007-01-01"), T("2010-01-01")),  # nothing on Yahoo
            ]
        )
    )
    first = {"DELL": T("2016-08-17"), "AAPL": T("1980-12-12"), "KHC": T("2015-07-06")}
    p = mb.mark_priced(s, first).set_index("ticker")["priced"]
    assert p.to_dict() == {"DELL": False, "AAPL": True, "KHC": True, "GONE": False}


def test_member_mask_looks_one_session_ahead_and_dedupes_share_classes():
    idx = pd.bdate_range("2024-03-11", "2024-03-22")
    s = MembershipTable(
        pd.DataFrame(
            [
                (
                    "A",
                    "A",
                    T("2024-03-11"),
                    T("2024-03-18"),
                ),  # leaves before Monday's open
                ("B", "B", T("2024-03-18"), pd.NaT),
                ("GOOG", "GOOG", T("2024-03-11"), pd.NaT),
                ("GOOGL", "GOOGL", T("2024-03-20"), pd.NaT),
            ],
            columns=mb.COLUMNS,
        ).assign(priced=True)
    )
    m = mb.member_mask(s, idx)
    # Friday's close (decision for Monday) already excludes A and includes B
    assert m.loc["2024-03-14", "A"] and not m.loc["2024-03-15", "A"]
    assert not m.loc["2024-03-14", "B"] and m.loc["2024-03-15", "B"]
    # GOOG counts until GOOGL joins (Wednesday open = Tuesday's close)
    assert m.loc["2024-03-18", "GOOG"] and not m.loc["2024-03-19", "GOOG"]
    assert m.loc["2024-03-19", "GOOGL"]
    # the last row looks to the next session on the calendar
    assert m.loc["2024-03-22", "B"]


def test_member_mask_is_empty_before_coverage():
    idx = pd.bdate_range("2006-12-20", "2007-01-05")
    s = MembershipTable(
        pd.DataFrame(
            [("A", "A", mb.COVERAGE_START, pd.NaT)], columns=mb.COLUMNS
        ).assign(priced=True)
    )
    m = mb.member_mask(s, idx)
    assert not m.loc[:"2006-12-28", "A"].any() and m.loc["2006-12-29":, "A"].all()


def test_save_load_roundtrip(tmp_path):
    s = mb.resolve(
        spells(
            [
                ("VIP", T("2013-10-29"), T("2015-12-21")),
                ("AAPL", T("2007-01-01"), pd.NaT),
            ]
        )
    )
    path = tmp_path / "m.csv"
    mb.save(s, path)
    back = mb.load(path)
    assert pd.isna(back.loc[back.ticker == "VIP", "symbol"].iloc[0])
    assert pd.isna(back.loc[back.ticker == "AAPL", "end"].iloc[0])
    assert back["start"].dtype.kind == "M"
