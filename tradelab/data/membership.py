"""Point-in-time Nasdaq-100 membership, so a backtest only holds what was in the index at the time.

Source: the yearly change files of github.com/jmccarrell/n100tickers (MIT licence), compiled from
Nasdaq's announcements and Wikipedia's "Historical components of the Nasdaq-100". Coverage starts in
2007. Each change is dated by its effective date (before that day's open), and tickers are the ones
that traded then (PCLN in 2010, not BKNG).

Prices come from Yahoo under today's symbols, so each membership spell is mapped to a Yahoo symbol.
``ALIASES`` maps renamed tickers onto the symbol that now carries their history (FB -> META, so a
rename is one continuous holding, not a sale and a purchase) and blanks out spells whose ticker
Yahoo now uses for a different company. A spell only counts as priced if its Yahoo history starts
by the spell's start (``mark_priced``): a later start means Yahoo dropped the company or gave the
ticker to someone else. Unpriced spells are the survivorship bias that remains, mostly companies
acquired or delisted years ago.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd
import requests
import yaml

from ..types import Mask, MembershipTable
from . import market_calendar as cal

SOURCE = "https://raw.githubusercontent.com/jmccarrell/n100tickers/HEAD/src/nasdaq_100_ticker_history"
FIRST_YEAR = 2007
COVERAGE_START = pd.Timestamp(FIRST_YEAR, 1, 1)
# a Yahoo history may start a few days after the spell (holidays, when-issued trading)
GRACE = pd.Timedelta(days=7)
COLUMNS = ["ticker", "symbol", "start", "end"]


@dataclass(frozen=True)
class Alias:
    # ticker as traded at the time
    ticker: str
    # Yahoo symbol carrying its history; None = Yahoo has no usable history
    symbol: str | None
    # only for spell days before this date (None = always)
    before: dt.date | None = None
    note: str = ""


ALIASES = [
    Alias("AEOS", "AEO", note="American Eagle moved to the NYSE as AEO"),
    Alias("CTRP", "TCOM", note="Ctrip became Trip.com Group"),
    Alias("DISCA", "WBD", note="Discovery became Warner Bros. Discovery"),
    Alias("ERTS", "EA", note="Electronic Arts ticker change"),
    Alias("FB", "META", note="Facebook became Meta; FB is now another security"),
    Alias("HANS", "MNST", note="Hansen Natural became Monster Beverage"),
    Alias("KFT", "MDLZ", note="Kraft Foods Inc. became Mondelez"),
    Alias("MYL", "VTRS", note="Mylan merged into Viatris"),
    Alias("NLOK", "GEN", note="NortonLifeLock became Gen Digital"),
    Alias("PCLN", "BKNG", note="Priceline became Booking; PCLN is another security"),
    Alias("RIMM", "BB", note="Research In Motion became BlackBerry"),
    Alias("SYMC", "GEN", note="Symantec became NortonLifeLock, then Gen Digital"),
    Alias("UAUA", "UAL", note="UAL Corp became United"),
    Alias("WLTW", "WTW", note="Willis Towers Watson ticker change"),
    # Tickers Yahoo now uses for a different company whose history covers the old spell.
    Alias("MNST", None, dt.date(2012, 1, 9), "Monster Worldwide, not Monster Beverage"),
    Alias("FOX", None, dt.date(2019, 3, 19), "21st Century Fox, not Fox Corp"),
    Alias("FOXA", None, dt.date(2019, 3, 19), "21st Century Fox, not Fox Corp"),
    Alias("NWSA", None, dt.date(2013, 7, 1), "News Corp before its 2013 split"),
    Alias("VIP", None, note="VimpelCom (now VEON); VIP is another company"),
]

# secondary share class -> primary: with ``dedupe`` only the primary counts while both are members
SHARE_CLASSES = {
    "GOOG": "GOOGL",
    "FOX": "FOXA",
    "LBTYK": "LBTYA",
    "LILAK": "LILA",
    "BATRK": "BATRA",
    "DISCK": "DISCA",
    "LMCK": "LMCA",
}


class Spell(NamedTuple):
    ticker: str
    symbol: str | None
    start: pd.Timestamp
    end: pd.Timestamp | None  # first day out of the index; None = still a member

    def live(self, days: pd.DatetimeIndex | np.ndarray) -> np.ndarray:
        on = np.asarray(days >= np.datetime64(self.start))
        return on if self.end is None else on & (days < np.datetime64(self.end))


def spells_of(table: MembershipTable) -> list[Spell]:
    return [
        Spell(
            str(t),
            None if pd.isna(s) else str(s),
            pd.Timestamp(a),
            None if pd.isna(b) else pd.Timestamp(b),
        )
        for t, s, a, b in zip(
            table["ticker"], table["symbol"], table["start"], table["end"]
        )
    ]


def table_of(spells: list[Spell]) -> MembershipTable:
    df = pd.DataFrame(spells, columns=COLUMNS)
    df["start"] = pd.to_datetime(df["start"])
    df["end"] = pd.to_datetime(df["end"])
    return MembershipTable(df)


def fetch_change_files(today: dt.date | None = None) -> list[str]:
    """The raw yearly YAML files, oldest first."""
    today = today or dt.date.today()
    docs = []
    for year in range(FIRST_YEAR, today.year + 1):
        r = requests.get(f"{SOURCE}/n100-ticker-changes-{year}.yaml", timeout=30)
        if r.status_code == 404 and year == today.year:
            break  # this year's file is not published until its first change
        r.raise_for_status()
        docs.append(r.text)
    return docs


def parse_changes(docs: list[str]) -> MembershipTable:
    """Membership spells from the yearly files (``symbol`` = ticker until ``resolve``)."""
    # BaseLoader keeps every scalar a string: YAML 1.1 would read the ticker ON as True
    years = [yaml.load(d, Loader=yaml.BaseLoader) for d in docs]
    first = pd.Timestamp(int(years[0]["year"]), 1, 1)
    since = {t: first for t in years[0]["tickers_on_Jan_1"]}
    out: list[Spell] = []
    for y in years:
        for day, change in sorted((y.get("changes") or {}).items()):
            d = pd.Timestamp(day)
            for t in change.get("difference") or []:
                if t not in since:
                    raise ValueError(f"{day}: {t} leaves the index but is not a member")
                out.append(Spell(t, t, since.pop(t), d))
            for t in change.get("union") or []:
                if t in since:
                    raise ValueError(
                        f"{day}: {t} joins the index but is already a member"
                    )
                since[t] = d
    out += [Spell(t, t, s, None) for t, s in since.items()]
    return table_of(sorted(out, key=lambda s: (s.start, s.ticker)))


def _symbol_on(ticker: str, day: pd.Timestamp) -> str | None:
    """The Yahoo symbol for ``ticker`` on ``day``: the most specific alias in force, else the ticker."""
    rules = [
        a
        for a in ALIASES
        if a.ticker == ticker and (a.before is None or day < pd.Timestamp(a.before))
    ]
    if not rules:
        return ticker
    return min(rules, key=lambda a: a.before or dt.date.max).symbol


def resolve(table: MembershipTable) -> MembershipTable:
    """Set the Yahoo ``symbol`` of each spell, splitting spells where an alias stops applying.

    Spells of one symbol that touch (a rename: FB ends the day META starts) are merged. Two spells of
    one symbol that overlap mean an alias is wrong, so that raises.
    """
    split: list[Spell] = []
    for r in spells_of(table):
        cuts = sorted(
            {
                pd.Timestamp(a.before)
                for a in ALIASES
                if a.ticker == r.ticker
                and a.before is not None
                and r.start < pd.Timestamp(a.before)
                and (r.end is None or pd.Timestamp(a.before) < r.end)
            }
        )
        for lo, hi in zip([r.start, *cuts], [*cuts, r.end]):
            split.append(Spell(r.ticker, _symbol_on(r.ticker, lo), lo, hi))
    merged: list[Spell] = []
    last: dict[str, int] = {}  # symbol -> its latest spell in ``merged``
    for r in sorted(split, key=lambda s: (s.start, s.ticker)):
        i = last.get(r.symbol) if r.symbol is not None else None
        if i is not None:
            prev = merged[i]
            if prev.end is None or r.start < prev.end:
                raise ValueError(
                    f"{r.symbol}: overlapping spells ({prev.ticker}, {r.ticker})"
                )
            if r.start == prev.end:  # contiguous: one holding under two tickers
                merged[i] = prev._replace(end=r.end)
                continue
        merged.append(r)
        if r.symbol is not None:
            last[r.symbol] = len(merged) - 1
    return table_of(merged)


def members_on(table: MembershipTable, day: pd.Timestamp) -> set[str]:
    return {
        s.symbol
        for s in spells_of(table)
        if s.symbol is not None and s.live(pd.DatetimeIndex([day]))[0]
    }


def reconcile(
    table: MembershipTable, current: list[str], today: dt.date
) -> tuple[MembershipTable, list[str]]:
    """Make today's membership match Nasdaq's live list (the history can lag announcements by weeks).

    Symbols Nasdaq lists that the history lacks join today; history members Nasdaq no longer lists
    leave today. Returns the fixed table and one note per difference.
    """
    day = pd.Timestamp(today)
    have, want = members_on(table, day), set(current)
    spells = [
        s._replace(end=day) if s.symbol in have - want and s.end is None else s
        for s in spells_of(table)
    ]
    spells += [Spell(s, s, day, None) for s in sorted(want - have)]
    notes = [
        f"{s} left the index (Nasdaq's list), not yet in the history"
        for s in sorted(have - want)
    ]
    notes += [
        f"{s} joined the index (Nasdaq's list), not yet in the history"
        for s in sorted(want - have)
    ]
    return table_of(spells), notes


def mark_priced(
    table: MembershipTable, first_bar: dict[str, pd.Timestamp]
) -> MembershipTable:
    """Add ``priced``: the spell's Yahoo history starts by the spell's start (within ``GRACE``)."""
    df = table.copy()
    df["priced"] = [
        s.symbol in first_bar and first_bar[s.symbol] <= s.start + GRACE
        for s in spells_of(table)
    ]
    return MembershipTable(df)


def member_mask(
    table: MembershipTable, index: pd.DatetimeIndex, dedupe: bool = True
) -> Mask:
    """True where a symbol is in the index at the session after each date (priced spells only).

    That is what a decision at a date's close may hold: Nasdaq announces changes days before they
    take effect, so the close before a removal already knows the stock must go.
    """
    after = pd.Timestamp(cal.next_session(index[-1].date()))
    nxt = pd.DatetimeIndex(index[1:].append(pd.DatetimeIndex([after])))
    priced = [s for s, ok in zip(spells_of(table), table["priced"]) if ok]
    out = pd.DataFrame(
        False, index=index, columns=sorted({str(s.symbol) for s in priced})
    )
    for s in priced:
        out[str(s.symbol)] |= s.live(nxt)
    if dedupe:
        for secondary, primary in SHARE_CLASSES.items():
            if secondary in out and primary in out:
                out.loc[out[primary], secondary] = False
    return Mask(out)


def coverage_by_year(table: MembershipTable, today: dt.date) -> pd.Series:
    """Share of member-days each year whose spell is priced (1.0 = no survivorship gap)."""
    days = pd.bdate_range(COVERAGE_START, today)
    total = np.zeros(len(days))
    priced = np.zeros(len(days))
    for s, ok in zip(spells_of(table), table["priced"]):
        live = s.live(days)
        total += live
        if ok:
            priced += live
    by_year = pd.DataFrame({"priced": priced, "total": total}).groupby(days.year).sum()
    return by_year["priced"] / by_year["total"]


def save(table: MembershipTable, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table[COLUMNS].to_csv(path, index=False, date_format="%Y-%m-%d")


def load(path: Path) -> MembershipTable:
    if not path.exists():
        raise FileNotFoundError(
            "No index membership history. Run: python -m tradelab fetch"
        )
    df = pd.read_csv(
        path, parse_dates=["start", "end"], keep_default_na=False, na_values=[""]
    )
    return table_of(spells_of(MembershipTable(df)))
