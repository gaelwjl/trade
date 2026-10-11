"""Fills and cash flows: the facts you enter. Every change is validated by replaying the whole
ledger (no overselling, splits included) and written to the audit log; deletes are soft.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..live import portfolio as pf
from . import market
from .models import AuditRow, CashFlowRow, FillRow, utcnow

FILL_FIELDS = ["trade_date", "side", "symbol", "qty", "price", "fees", "strategy"]
FILL_FIELDS += ["suggestion_id", "note"]
CASH_FIELDS = ["date", "kind", "amount", "note"]


def _as_dict(row: FillRow | CashFlowRow, fields: list[str]) -> dict:
    out = {f: getattr(row, f) for f in fields}
    return {k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in out.items()}


def _audit(s: Session, entity: str, entity_id: int, action: str, before, after) -> None:  # type: ignore[no-untyped-def]
    s.add(
        AuditRow(
            entity=entity,
            entity_id=entity_id,
            action=action,
            before=before,
            after=after,
        )
    )


# ---------- reads ----------
def fills(s: Session) -> list[FillRow]:
    q = select(FillRow).where(FillRow.deleted_at.is_(None))
    return list(s.scalars(q.order_by(FillRow.trade_date, FillRow.id)))


def cash_flows(s: Session) -> list[CashFlowRow]:
    q = select(CashFlowRow).where(CashFlowRow.deleted_at.is_(None))
    return list(s.scalars(q.order_by(CashFlowRow.date, CashFlowRow.id)))


def to_fill(r: FillRow) -> pf.Fill:
    return pf.Fill(
        r.trade_date, r.side, r.symbol, r.qty, r.price, r.fees, r.strategy, r.id
    )


def to_cash(r: CashFlowRow) -> pf.CashFlow:
    return pf.CashFlow(r.date, r.kind, r.amount, r.id)


def records(
    s: Session,
) -> tuple[list[pf.Fill], list[pf.CashFlow], list[pf.CorporateAction]]:
    """Everything the P&L engine needs, as plain records."""
    fs = [to_fill(r) for r in fills(s)]
    return fs, [to_cash(r) for r in cash_flows(s)], market.actions(s, traded_symbols(s))


def traded_symbols(s: Session) -> list[str]:
    return sorted({r.symbol for r in fills(s)})


def _check(s: Session, fs: list[pf.Fill], cs: list[pf.CashFlow]) -> None:
    pf.validate(fs, cs, market.actions(s, sorted({f.symbol for f in fs})))


# ---------- writes ----------
def make_fill(
    trade_date: dt.date,
    side: str,
    symbol: str,
    qty: float,
    price: float,
    fees: float = 0.0,
    strategy: str = "",
    note: str = "",
    suggestion_id: int | None = None,
) -> FillRow:
    side, symbol = side.upper(), symbol.upper().strip()
    if side not in ("BUY", "SELL"):
        raise pf.LedgerError("side must be BUY or SELL")
    if not (qty > 0 and price > 0) or fees < 0:
        raise pf.LedgerError("qty and price must be positive, fees non-negative")
    if trade_date > dt.date.today() + dt.timedelta(days=1):
        raise pf.LedgerError("trade date is in the future")
    if not symbol:
        raise pf.LedgerError("symbol is required")
    return FillRow(
        trade_date=trade_date,
        side=side,
        symbol=symbol,
        qty=qty,
        price=price,
        fees=fees,
        strategy=strategy,
        note=note,
        suggestion_id=suggestion_id,
    )


def add_fill(s: Session, row: FillRow) -> FillRow:
    current = [to_fill(r) for r in fills(s)]
    _check(s, [*current, to_fill(row)], [to_cash(r) for r in cash_flows(s)])
    s.add(row)
    s.flush()
    _audit(s, "fill", row.id, "create", None, _as_dict(row, FILL_FIELDS))
    return row


def update_fill(s: Session, fill_id: int, changes: dict) -> FillRow:
    row = s.get(FillRow, fill_id)
    if row is None or row.deleted_at is not None:
        raise KeyError(fill_id)
    before = _as_dict(row, FILL_FIELDS)
    candidate = make_fill(
        **{
            "trade_date": row.trade_date,
            "side": row.side,
            "symbol": row.symbol,
            "qty": row.qty,
            "price": row.price,
            "fees": row.fees,
            "strategy": row.strategy,
            "note": row.note,
            "suggestion_id": row.suggestion_id,
            **changes,
        }
    )
    others = [to_fill(r) for r in fills(s) if r.id != fill_id]
    new = pf.Fill(
        candidate.trade_date,
        candidate.side,
        candidate.symbol,
        candidate.qty,
        candidate.price,
        candidate.fees,
        candidate.strategy,
        fill_id,
    )
    _check(s, [*others, new], [to_cash(r) for r in cash_flows(s)])
    for f in FILL_FIELDS:
        setattr(row, f, getattr(candidate, f))
    _audit(s, "fill", fill_id, "update", before, _as_dict(row, FILL_FIELDS))
    return row


def delete_fill(s: Session, fill_id: int) -> FillRow:
    row = s.get(FillRow, fill_id)
    if row is None or row.deleted_at is not None:
        raise KeyError(fill_id)
    others = [to_fill(r) for r in fills(s) if r.id != fill_id]
    _check(
        s, others, [to_cash(r) for r in cash_flows(s)]
    )  # e.g. deleting a buy you sold
    row.deleted_at = utcnow()
    _audit(s, "fill", fill_id, "delete", _as_dict(row, FILL_FIELDS), None)
    return row


def add_cash(
    s: Session, date: dt.date, kind: str, amount: float, note: str = ""
) -> CashFlowRow:
    if kind not in pf.CASH_KINDS:
        raise pf.LedgerError(f"kind must be one of {sorted(pf.CASH_KINDS)}")
    if amount <= 0:
        raise pf.LedgerError("amount must be positive (the kind sets the sign)")
    row = CashFlowRow(date=date, kind=kind, amount=amount, note=note)
    s.add(row)
    s.flush()
    _audit(s, "cash", row.id, "create", None, _as_dict(row, CASH_FIELDS))
    return row


def delete_cash(s: Session, cash_id: int) -> CashFlowRow:
    row = s.get(CashFlowRow, cash_id)
    if row is None or row.deleted_at is not None:
        raise KeyError(cash_id)
    row.deleted_at = utcnow()
    _audit(s, "cash", cash_id, "delete", _as_dict(row, CASH_FIELDS), None)
    return row


def last_fill(s: Session) -> FillRow | None:
    rows = fills(s)
    return max(rows, key=lambda r: r.id) if rows else None


def audit_log(s: Session, limit: int = 100) -> list[AuditRow]:
    q = select(AuditRow).order_by(AuditRow.id.desc()).limit(limit)
    return list(s.scalars(q))
