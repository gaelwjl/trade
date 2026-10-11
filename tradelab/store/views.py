"""Read models: the stored ledger + raw closes run through the P&L engine, as the API shows them."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd
from sqlalchemy.orm import Session

from ..data import market_calendar as cal
from ..data.prices import Panel, price_path
from ..live import portfolio as pf
from ..types import PositionTable, SnapshotTable
from . import ledger, market


@dataclass
class PortfolioView:
    snapshots: SnapshotTable
    positions: PositionTable
    book: pf.Book
    as_of: dt.date | None  # last session valued
    missing_prices: list[str]  # held symbols valued at their last fill price

    @property
    def equity(self) -> float:
        return float(self.snapshots.equity.iloc[-1]) if len(self.snapshots) else 0.0


def portfolio(s: Session, today: dt.date | None = None) -> PortfolioView:
    fills, flows, actions = ledger.records(s)
    symbols = ledger.traded_symbols(s)
    closes = market.closes(s, symbols)
    events = [f.date for f in fills] + [c.date for c in flows]
    if not events:
        empty, book = pf.snapshots([], [], [], closes, pd.DatetimeIndex([]))
        return PortfolioView(empty, pf.positions(book, {}, 0.0), book, None, [])
    last_close = closes.index[-1].date() if len(closes) else None
    end = max([d for d in (last_close, max(events)) if d is not None])
    if today is not None:
        end = min(end, today)
    days = cal.sessions(min(events), end)
    if not days or days[-1] < max(events):  # e.g. a fill entered on a holiday
        days.append(max(events))
    snap, book = pf.snapshots(
        fills, flows, actions, closes, pd.DatetimeIndex(pd.to_datetime(days))
    )
    as_of = snap.index[-1] if len(snap) else None
    prices, missing = {}, []
    for sym in book.open_symbols():
        if sym in closes and closes[sym].dropna().size:
            prices[sym] = float(closes[sym].dropna().iloc[-1])
        else:
            missing.append(sym)
            prices[sym] = next(f.price for f in reversed(fills) if f.symbol == sym)
    equity = float(snap.equity.iloc[-1]) if len(snap) else 0.0
    return PortfolioView(snap, pf.positions(book, prices, equity), book, as_of, missing)


def benchmark(dates: list[dt.date], symbol: str = "QQQ") -> pd.Series:
    """Total-return index of ``symbol`` on ``dates`` (1.0 on the first date)."""
    if not dates or not price_path(symbol).exists():
        return pd.Series(dtype=float)
    close = Panel.load([symbol]).close[symbol].dropna()
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    aligned = close.reindex(close.index.union(idx)).ffill().reindex(idx)
    return aligned / aligned.dropna().iloc[0] if aligned.notna().any() else aligned
