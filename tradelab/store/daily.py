"""What the daily job writes: targets, suggestions, portfolio and paper snapshots."""

from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..live.suggestions import Order
from ..types import SnapshotTable
from .models import (
    FillRow,
    PaperSnapshotRow,
    PortfolioSnapshotRow,
    SignalRunRow,
    SuggestionRow,
    TargetRow,
    WatchlistRow,
)


# ---------- targets ----------
def save_targets(
    s: Session,
    day: dt.date,
    config_id: int,
    is_rebalance: bool,
    next_rebalance: dt.date,
    weights: dict[str, float],
    scores: dict[str, float | None],
) -> None:
    """Replace one strategy's target for one session (idempotent)."""
    s.execute(
        delete(TargetRow).where(TargetRow.date == day, TargetRow.config_id == config_id)
    )
    s.execute(
        delete(SignalRunRow).where(
            SignalRunRow.date == day, SignalRunRow.config_id == config_id
        )
    )
    s.add(
        SignalRunRow(
            date=day,
            config_id=config_id,
            is_rebalance=is_rebalance,
            next_rebalance=next_rebalance,
        )
    )
    s.add_all(
        TargetRow(
            date=day, config_id=config_id, symbol=sym, weight=w, score=scores.get(sym)
        )
        for sym, w in weights.items()
    )


def target_rows(s: Session, day: dt.date, config_id: int) -> list[TargetRow]:
    """Target rows, largest weight first."""
    q = select(TargetRow).where(TargetRow.date == day, TargetRow.config_id == config_id)
    return sorted(s.scalars(q), key=lambda t: -t.weight)


def targets(s: Session, day: dt.date, config_id: int) -> dict[str, float]:
    q = select(TargetRow.symbol, TargetRow.weight).where(
        TargetRow.date == day, TargetRow.config_id == config_id
    )
    return {sym: w for sym, w in s.execute(q)}


def signal_run(
    s: Session, config_id: int, day: dt.date | None = None
) -> SignalRunRow | None:
    q = select(SignalRunRow).where(SignalRunRow.config_id == config_id)
    if day is not None:
        q = q.where(SignalRunRow.date == day)
    return s.scalar(q.order_by(SignalRunRow.date.desc()).limit(1))


# ---------- suggestions ----------
def replace_suggestions(
    s: Session,
    signal_date: dt.date,
    for_session: dt.date,
    config_id: int,
    orders: list[Order],
) -> list[SuggestionRow]:
    """Write a session's suggestions (re-running the day replaces its untouched ones) and
    expire every older suggestion still open."""
    s.execute(
        update(SuggestionRow)
        .where(SuggestionRow.signal_date < signal_date, SuggestionRow.status == "open")
        .values(status="expired")
    )
    s.execute(
        delete(SuggestionRow).where(
            SuggestionRow.signal_date == signal_date,
            SuggestionRow.status == "open",
            SuggestionRow.filled_qty == 0,
        )
    )
    rows = [
        SuggestionRow(
            signal_date=signal_date,
            for_session=for_session,
            config_id=config_id,
            symbol=o.symbol,
            action=o.action,
            side=o.side,
            qty=o.qty,
            ref_price=o.ref_price,
            target_weight=o.target_weight,
            held_qty=o.held_qty,
            reason=o.reason,
        )
        for o in orders
    ]
    s.add_all(rows)
    s.flush()
    return rows


def suggestions(
    s: Session, signal_date: dt.date | None = None, status: str | None = None
) -> list[SuggestionRow]:
    q = select(SuggestionRow)
    if signal_date is not None:
        q = q.where(SuggestionRow.signal_date == signal_date)
    if status is not None:
        q = q.where(SuggestionRow.status == status)
    return list(s.scalars(q.order_by(SuggestionRow.side.desc(), SuggestionRow.symbol)))


def latest_signal_date(s: Session) -> dt.date | None:
    return s.scalar(
        select(SuggestionRow.signal_date)
        .order_by(SuggestionRow.signal_date.desc())
        .limit(1)
    )


def refresh_status(s: Session, suggestion: SuggestionRow) -> None:
    """Recompute filled quantity from linked fills: done, partial or still open."""
    linked = s.scalars(
        select(FillRow).where(
            FillRow.suggestion_id == suggestion.id, FillRow.deleted_at.is_(None)
        )
    )
    filled = sum(f.qty for f in linked if f.side == suggestion.side)
    suggestion.filled_qty = filled
    if suggestion.status in ("dismissed", "expired") and filled == 0:
        return
    if filled >= suggestion.qty - 1e-9:
        suggestion.status = "done"
    elif filled > 0:
        suggestion.status = "partial"
    else:
        suggestion.status = "open"


def match_unlinked_fills(s: Session, signal_date: dt.date) -> None:
    """Link fills entered by hand to that session's open suggestion with the same symbol and side."""
    for sug in suggestions(s, signal_date):
        if sug.status not in ("open", "partial"):
            continue
        q = select(FillRow).where(
            FillRow.suggestion_id.is_(None),
            FillRow.deleted_at.is_(None),
            FillRow.symbol == sug.symbol,
            FillRow.side == sug.side,
            FillRow.trade_date >= sug.for_session,
            FillRow.trade_date <= sug.for_session + dt.timedelta(days=4),
        )
        for f in s.scalars(q):
            f.suggestion_id = sug.id
        refresh_status(s, sug)


# ---------- snapshots ----------
def save_portfolio_snapshots(s: Session, table: SnapshotTable) -> None:
    s.execute(delete(PortfolioSnapshotRow))
    s.add_all(
        PortfolioSnapshotRow(date=d, **{str(k): float(v) for k, v in r.items()})
        for d, r in table.iterrows()
    )


def save_paper(
    s: Session, config_id: int, equity: pd.Series, positions: dict[str, float]
) -> None:
    s.execute(delete(PaperSnapshotRow).where(PaperSnapshotRow.config_id == config_id))
    last = equity.index[-1] if len(equity) else None
    s.add_all(
        PaperSnapshotRow(
            date=d.date(),
            config_id=config_id,
            equity=float(v),
            positions=positions if d == last else {},
        )
        for d, v in zip(pd.DatetimeIndex(equity.index), equity)
    )


def paper(s: Session, config_id: int) -> pd.Series:
    q = select(PaperSnapshotRow.date, PaperSnapshotRow.equity).where(
        PaperSnapshotRow.config_id == config_id
    )
    rows = s.execute(q.order_by(PaperSnapshotRow.date)).all()
    return pd.Series({d: e for d, e in rows}, dtype=float)


# ---------- watchlist ----------
def watchlist(s: Session) -> list[str]:
    return list(s.scalars(select(WatchlistRow.symbol).order_by(WatchlistRow.symbol)))


def set_watchlist(s: Session, symbols: list[str]) -> list[str]:
    s.execute(delete(WatchlistRow))
    clean = sorted({x.upper().strip() for x in symbols if x.strip()})
    s.add_all(WatchlistRow(symbol=x) for x in clean)
    return clean
