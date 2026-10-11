"""Portfolio accounting: FIFO lots, cash, splits, dividends, daily valuation and returns.

Pure functions over plain records; persistence lives in ``tradelab.store``. Valuation uses RAW
(unadjusted) closes so it matches what the market quoted; splits and dividends come from
corporate actions. Trade fees go into cost basis (buys) and proceeds (sells).

Identity checked on every snapshot:
    realized + unrealized + dividends + other (interest - account fees) = equity - net deposits
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

import pandas as pd

from ..types import PositionTable, PriceFrame, SnapshotTable

EXTERNAL = {"deposit": 1.0, "withdrawal": -1.0}  # money in/out: excluded from returns
OTHER = {"interest": 1.0, "fee": -1.0}  # account income/costs: part of P&L
CASH_KINDS = {**EXTERNAL, **OTHER}
EPS = 1e-9


class LedgerError(ValueError):
    """The ledger is inconsistent (e.g. selling more than you hold)."""


@dataclass(frozen=True)
class Fill:
    date: dt.date
    side: str  # BUY | SELL
    symbol: str
    qty: float
    price: float
    fees: float = 0.0
    strategy: str = ""
    id: int | None = None


@dataclass(frozen=True)
class CashFlow:
    date: dt.date
    kind: str  # deposit | withdrawal | fee | interest
    amount: float  # positive; the kind sets the sign
    id: int | None = None


@dataclass(frozen=True)
class CorporateAction:
    symbol: str
    ex_date: dt.date
    kind: str  # split | dividend
    value: float  # new shares per old share, or raw cash per share


@dataclass
class Lot:
    symbol: str
    open_date: dt.date
    qty: float
    cost_per_share: float
    strategy: str
    fill_id: int | None


@dataclass(frozen=True)
class Realized:
    date: dt.date
    symbol: str
    qty: float
    proceeds: float  # net of fees
    cost: float
    strategy: str

    @property
    def pnl(self) -> float:
        return self.proceeds - self.cost


@dataclass
class Book:
    """Running state while replaying the ledger."""

    lots: dict[str, list[Lot]] = field(default_factory=lambda: defaultdict(list))
    cash: float = 0.0
    deposits: float = 0.0  # net external flows
    dividends: float = 0.0
    other: float = 0.0  # interest - account fees
    realized: list[Realized] = field(default_factory=list)

    def qty(self, symbol: str) -> float:
        return sum(lot.qty for lot in self.lots.get(symbol, []))

    @property
    def realized_total(self) -> float:
        return sum(r.pnl for r in self.realized)

    def open_symbols(self) -> list[str]:
        return sorted(
            s for s, lots in self.lots.items() if sum(l.qty for l in lots) > EPS
        )

    # --- events ---
    def split(self, symbol: str, ratio: float) -> None:
        for lot in self.lots.get(symbol, []):
            lot.qty *= ratio
            lot.cost_per_share /= ratio

    def dividend(self, symbol: str, per_share: float) -> None:
        amount = self.qty(symbol) * per_share
        self.cash += amount
        self.dividends += amount

    def cash_flow(self, cf: CashFlow) -> float:
        """Apply a cash flow; returns its external part (for returns)."""
        if cf.kind not in CASH_KINDS:
            raise LedgerError(f"unknown cash flow kind {cf.kind!r}")
        signed = CASH_KINDS[cf.kind] * cf.amount
        self.cash += signed
        if cf.kind in EXTERNAL:
            self.deposits += signed
            return signed
        self.other += signed
        return 0.0

    def fill(self, f: Fill) -> None:
        if f.side == "BUY":
            cost = f.qty * f.price + f.fees
            self.cash -= cost
            self.lots[f.symbol].append(
                Lot(f.symbol, f.date, f.qty, cost / f.qty, f.strategy, f.id)
            )
            return
        held = self.qty(f.symbol)
        if f.qty > held + EPS:
            raise LedgerError(
                f"{f.date}: selling {f.qty:g} {f.symbol} but only {held:g} held"
            )
        self.cash += f.qty * f.price - f.fees
        remaining, cost, strategies = f.qty, 0.0, []
        lots = self.lots[f.symbol]
        while remaining > EPS:  # first in, first out
            lot = lots[0]
            take = min(lot.qty, remaining)
            cost += take * lot.cost_per_share
            strategies.append(lot.strategy)
            lot.qty -= take
            remaining -= take
            if lot.qty <= EPS:
                lots.pop(0)
        self.realized.append(
            Realized(
                f.date,
                f.symbol,
                f.qty,
                f.qty * f.price - f.fees,
                cost,
                f.strategy or next((s for s in strategies if s), ""),
            )
        )


def _events_by_date(
    fills: list[Fill], flows: list[CashFlow], actions: list[CorporateAction]
) -> dict[dt.date, tuple[list[CorporateAction], list[CashFlow], list[Fill]]]:
    days: dict[dt.date, tuple[list, list, list]] = defaultdict(lambda: ([], [], []))
    for a in actions:
        days[a.ex_date][0].append(a)
    for c in flows:
        days[c.date][1].append(c)
    for f in fills:
        days[f.date][2].append(f)
    return days


def _apply_day(
    book: Book,
    actions: list[CorporateAction],
    flows: list[CashFlow],
    fills: list[Fill],
) -> float:
    """One day's events in a fixed order; returns that day's external flow.

    Splits and dividends go first: they belong to shares held before the day's trades
    (a dividend is credited on its ex-date; Yahoo does not publish pay dates).
    """
    for a in actions:
        if a.kind == "split":
            book.split(a.symbol, a.value)
    for a in actions:
        if a.kind == "dividend":
            book.dividend(a.symbol, a.value)
    external = sum(book.cash_flow(c) for c in flows)
    for f in sorted(fills, key=lambda f: (f.id is None, f.id or 0)):
        book.fill(f)
    return external


def replay(
    fills: list[Fill],
    flows: list[CashFlow] = (),  # type: ignore[assignment]
    actions: list[CorporateAction] = (),  # type: ignore[assignment]
    until: dt.date | None = None,
) -> Book:
    """Book state after every event up to and including ``until``. Raises ``LedgerError``."""
    book = Book()
    days = _events_by_date(list(fills), list(flows), list(actions))
    first = min((f.date for f in fills), default=None)
    for day in sorted(days):
        if until is not None and day > until:
            break
        acts, cfs, fs = days[day]
        # corporate actions before the first fill can't affect anything; skip the noise
        if first is None or day < first:
            acts = []
        _apply_day(book, acts, cfs, fs)
    return book


def validate(
    fills: list[Fill], flows: list[CashFlow], actions: list[CorporateAction]
) -> None:
    """Raise ``LedgerError`` if the ledger cannot be replayed."""
    replay(fills, flows, actions)


def _price_on(closes: PriceFrame, symbol: str, day: pd.Timestamp) -> float | None:
    if symbol not in closes:
        return None
    s = closes[symbol].loc[:day].dropna()
    return float(s.iloc[-1]) if len(s) else None


def snapshots(
    fills: list[Fill],
    flows: list[CashFlow],
    actions: list[CorporateAction],
    closes: PriceFrame,
    sessions: pd.DatetimeIndex,
) -> tuple[SnapshotTable, Book]:
    """One row per session from the first ledger event to the last session.

    ``closes`` are raw closes (date x symbol); a symbol without a close yet is valued at its
    last fill price. Returns the table and the final book.
    """
    book = Book()
    days = _events_by_date(fills, flows, actions)
    if not days:
        return SnapshotTable(pd.DataFrame(columns=SNAPSHOT_COLUMNS)), book
    start = min(d for d, (_, cfs, fs) in days.items() if cfs or fs)
    fill_price: dict[str, float] = {}
    session_days = [d.date() for d in sessions if d.date() >= start]
    timeline = sorted(set(session_days) | {d for d in days if d >= start})
    session_set = set(session_days)

    rows, prev_equity, flows_since, twr = [], 0.0, 0.0, 1.0
    for day in timeline:
        acts, cfs, fs = days.get(day, ([], [], []))
        flows_since += _apply_day(book, acts, cfs, fs)
        for f in fs:
            fill_price[f.symbol] = f.price
        if day not in session_set:
            continue
        ts = pd.Timestamp(day)
        mv = unrealized = 0.0
        for sym in book.open_symbols():
            px = _price_on(closes, sym, ts)
            px = px if px is not None else fill_price[sym]
            for lot in book.lots[sym]:
                mv += lot.qty * px
                unrealized += lot.qty * (px - lot.cost_per_share)
        equity = book.cash + mv
        day_pnl = equity - prev_equity - flows_since
        base = prev_equity + flows_since
        twr *= 1 + (day_pnl / base if base > EPS else 0.0)
        rows.append(
            {
                "date": day,
                "market_value": mv,
                "cash": book.cash,
                "equity": equity,
                "net_flows": flows_since,
                "deposits_cum": book.deposits,
                "realized_cum": book.realized_total,
                "unrealized": unrealized,
                "dividends_cum": book.dividends,
                "other_cum": book.other,
                "day_pnl": day_pnl,
                "twr_index": twr,
            }
        )
        prev_equity, flows_since = equity, 0.0
    table = pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS).set_index("date")
    return SnapshotTable(table), book


SNAPSHOT_COLUMNS = [
    "date",
    "market_value",
    "cash",
    "equity",
    "net_flows",
    "deposits_cum",
    "realized_cum",
    "unrealized",
    "dividends_cum",
    "other_cum",
    "day_pnl",
    "twr_index",
]


def identity_gap(snap: SnapshotTable) -> pd.Series:
    """Per-row difference between the two sides of the P&L identity (should be ~0)."""
    lhs = snap.realized_cum + snap.unrealized + snap.dividends_cum + snap.other_cum
    return lhs - (snap.equity - snap.deposits_cum)


def positions(book: Book, prices: dict[str, float], equity: float) -> PositionTable:
    """Open positions aggregated per symbol, valued at ``prices``."""
    rows = []
    for sym in book.open_symbols():
        lots = book.lots[sym]
        qty = sum(l.qty for l in lots)
        cost = sum(l.qty * l.cost_per_share for l in lots)
        px = prices.get(sym)
        value = qty * px if px is not None else None
        rows.append(
            {
                "symbol": sym,
                "qty": qty,
                "avg_cost": cost / qty,
                "cost": cost,
                "last": px,
                "value": value,
                "pnl": value - cost if value is not None else None,
                "pnl_pct": (
                    (value - cost) / cost if value is not None and cost else None
                ),
                "weight": (
                    value / equity if value is not None and equity > EPS else None
                ),
                "opened": min(l.open_date for l in lots),
                "strategies": ",".join(
                    sorted({l.strategy for l in lots if l.strategy})
                ),
            }
        )
    cols = ["qty", "avg_cost", "cost", "last", "value", "pnl", "pnl_pct", "weight"]
    cols += ["opened", "strategies"]
    return PositionTable(
        pd.DataFrame(rows, columns=["symbol", *cols]).set_index("symbol")
    )


def strategy_holdings(book: Book, strategy: str) -> dict[str, float]:
    """Shares held in lots tagged with ``strategy``."""
    out: dict[str, float] = {}
    for sym in book.open_symbols():
        q = sum(l.qty for l in book.lots[sym] if l.strategy == strategy)
        if q > EPS:
            out[sym] = q
    return out
