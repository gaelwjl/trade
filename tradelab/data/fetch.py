"""Refresh the cache: Nasdaq-100 constituents, membership history and full adjusted price history
from Yahoo Finance (for former members too)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd
import requests

from ..types import Bars, MembershipTable, PriceFrame, QualityTable
from . import membership, prices


@dataclass(frozen=True)
class FetchReport:
    constituents: int
    as_of: str
    requested: list[str]
    missing: list[str]  # current members, index symbols or holdings Yahoo returned nothing for
    quality: QualityTable  # first/last date, rows, interior gaps, largest daily move, zero-volume days
    membership: MembershipTable  # spells with ``priced``
    coverage: pd.Series  # by year: share of member-days with usable prices
    notes: list[str]  # membership source problems, history vs Nasdaq's list
    kept_cache: list[str]  # Yahoo returned less history than the cache had, cache kept


def refresh_membership(
    current: list[str], today: dt.date
) -> tuple[MembershipTable, list[str]]:
    """Rebuild ``membership.csv`` from the source, matched to Nasdaq's live list.

    If the source can't be reached, the previous file is kept (and noted).
    """
    try:
        spells = membership.resolve(
            membership.parse_changes(membership.fetch_change_files(today))
        )
    except requests.RequestException as e:
        if not prices.MEMBERSHIP_PATH.exists():
            raise
        spells = membership.load(prices.MEMBERSHIP_PATH)
        return spells, [f"membership source unreachable, kept the previous file ({e})"]
    spells, notes = membership.reconcile(spells, current, today)
    membership.save(spells, prices.MEMBERSHIP_PATH)
    return spells, notes


def stale_former_members(
    spells: MembershipTable, skip: set[str], today: dt.date
) -> list[str]:
    """Former members to download: not cached yet, or left the index in the last 30 days and the
    cache doesn't reach the exit. Older histories don't change except for re-adjustment, which
    leaves returns alone."""
    left: dict[str, pd.Timestamp | None] = {}  # symbol -> last exit (None = current)
    for sp in membership.spells_of(spells):
        if sp.symbol is not None and sp.symbol not in skip:
            prev = left.get(sp.symbol, sp.end)
            left[sp.symbol] = (
                None if prev is None or sp.end is None else max(prev, sp.end)
            )
    recent = pd.Timestamp(today) - pd.Timedelta(days=30)
    out = []
    for s, end in left.items():
        path = prices.price_path(s)
        if not path.exists():
            out.append(s)
        elif end is None or end > recent:
            if pd.read_parquet(path, columns=["Close"]).index[-1] < (end or recent):
                out.append(s)
    return sorted(out)


def refresh(extra_symbols: list[str] | None = None) -> FetchReport:
    """Download constituents, membership history, price history for every member since 2007,
    index/ETF history and ``extra_symbols`` (e.g. holdings outside the index)."""
    today = dt.date.today()
    cons = prices.fetch_constituents()
    prices.DATA_DIR.mkdir(parents=True, exist_ok=True)
    cons.to_csv(prices.CONSTITUENTS_PATH, index=False)
    spells, notes = refresh_membership(cons["symbol"].tolist(), today)

    required = cons["symbol"].tolist() + prices.INDEX_SYMBOLS
    required += [s for s in (extra_symbols or []) if s not in required]
    former = stale_former_members(spells, set(required), today)
    frames = prices.download_prices(required + former)
    kept = prices.save_prices(frames)
    first = {}
    for s in {sp.symbol for sp in membership.spells_of(spells) if sp.symbol}:
        if prices.price_path(s).exists():
            first[s] = pd.read_parquet(prices.price_path(s), columns=["Close"]).index[0]
    spells = membership.mark_priced(spells, first)
    return FetchReport(
        constituents=len(cons),
        as_of=str(cons["as_of"].iloc[0]),
        requested=required + former,
        missing=[s for s in required if s not in frames],
        quality=quality(frames),
        membership=spells,
        coverage=membership.coverage_by_year(spells, today),
        notes=notes,
        kept_cache=kept,
    )


def quality(frames: dict[str, Bars]) -> QualityTable:
    stocks = PriceFrame(
        pd.DataFrame(
            {
                s: df["Close"]
                for s, df in frames.items()
                if s not in prices.INDEX_SYMBOLS
            }
        )
    )
    gaps = prices.interior_gaps(
        stocks
    ).sum()  # days the other stocks traded but this one has no bar
    rows = []
    for s, df in frames.items():
        r = df["Close"].pct_change(fill_method=None)
        rows.append(
            {
                "symbol": s,
                "first": df.index[0].date(),
                "last": df.index[-1].date(),
                "rows": len(df),
                "gaps": int(gaps.get(s, 0)),
                "max_abs_ret": r.abs().max(),
                "zero_vol_days": (
                    int((df["Volume"] == 0).sum()) if "Volume" in df else 0
                ),
            }
        )
    return QualityTable(pd.DataFrame(rows).set_index("symbol").sort_values("first"))
