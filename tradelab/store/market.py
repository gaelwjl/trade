"""Raw (unadjusted) closes and corporate actions used to value your portfolio."""

from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..data.prices import RawHistory
from ..live.portfolio import CorporateAction
from ..types import PriceFrame
from .models import CorporateActionRow, RawCloseRow


def save_raw(s: Session, symbol: str, hist: RawHistory) -> None:
    """Replace a symbol's stored history (full refresh keeps restatements in sync)."""
    s.execute(delete(RawCloseRow).where(RawCloseRow.symbol == symbol))
    s.execute(delete(CorporateActionRow).where(CorporateActionRow.symbol == symbol))
    s.add_all(
        RawCloseRow(symbol=symbol, date=d.date(), close=float(c))
        for d, c in zip(pd.DatetimeIndex(hist.close.index), hist.close)
    )
    seen = set()
    for ex, kind, value in hist.actions:
        if (ex, kind) in seen:  # Yahoo occasionally repeats an event
            continue
        seen.add((ex, kind))
        s.add(CorporateActionRow(symbol=symbol, ex_date=ex, kind=kind, value=value))


def closes(s: Session, symbols: list[str], since: dt.date | None = None) -> PriceFrame:
    """Raw closes, date x symbol."""
    if not symbols:
        return PriceFrame(pd.DataFrame())
    q = select(RawCloseRow.date, RawCloseRow.symbol, RawCloseRow.close).where(
        RawCloseRow.symbol.in_(symbols)
    )
    if since is not None:
        q = q.where(RawCloseRow.date >= since)
    rows = s.execute(q).all()
    if not rows:
        return PriceFrame(pd.DataFrame(columns=symbols))
    df = pd.DataFrame(rows, columns=["date", "symbol", "close"])
    wide = df.pivot(index="date", columns="symbol", values="close")
    wide.index = pd.DatetimeIndex(wide.index)
    return PriceFrame(wide.sort_index())


def actions(s: Session, symbols: list[str]) -> list[CorporateAction]:
    if not symbols:
        return []
    q = select(CorporateActionRow).where(CorporateActionRow.symbol.in_(symbols))
    return [
        CorporateAction(r.symbol, r.ex_date, r.kind, r.value)
        for r in s.scalars(q.order_by(CorporateActionRow.ex_date))
    ]
