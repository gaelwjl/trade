"""Today, portfolio, P&L history, fills and cash."""

from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..data.prices import last_cached_date
from ..live.signals import expected_last_session
from ..store import daily as daily_store
from ..store import ledger, runs, views
from ..store import strategies as strat_store
from ..store.db import must_get
from ..store.models import CashFlowRow, FillRow, StrategyConfigRow
from ..types import PositionTable
from .deps import db, iso, row_dict
from .schemas import CashIn, FillIn, FillPatch

router = APIRouter()

FILL_COLS = ["id", "trade_date", "side", "symbol", "qty", "price", "fees", "strategy"]
FILL_COLS += ["suggestion_id", "note", "created_at"]
CASH_COLS = ["id", "date", "kind", "amount", "note", "created_at"]
SUGGESTION_COLS = ["id", "signal_date", "for_session", "symbol", "action", "side"]
SUGGESTION_COLS += ["qty", "ref_price", "target_weight", "held_qty", "reason", "status"]
SUGGESTION_COLS += ["filled_qty"]


def data_last_date() -> str | None:
    last = last_cached_date()
    return last.isoformat() if last else None


def _num(v: object) -> object:
    if v is None:
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return iso(v)
    return None if pd.isna(f) else f


def records(df: PositionTable) -> list[dict]:
    return [
        {k: _num(v) for k, v in r.items()} for r in df.reset_index().to_dict("records")
    ]


def totals(view: views.PortfolioView) -> dict:
    snap = view.snapshots
    if not len(snap):
        return {"equity": 0.0, "cash": 0.0, "market_value": 0.0, "deposits": 0.0}
    last = snap.iloc[-1]
    bench = views.benchmark(list(snap.index))
    return {
        "as_of": iso(view.as_of),
        "equity": float(last.equity),
        "cash": float(last.cash),
        "market_value": float(last.market_value),
        "deposits": float(last.deposits_cum),
        "day_pnl": float(last.day_pnl),
        "total_pnl": float(last.equity - last.deposits_cum),
        "realized": float(last.realized_cum),
        "unrealized": float(last.unrealized),
        "dividends": float(last.dividends_cum),
        "twr": float(last.twr_index - 1),
        "qqq": float(bench.iloc[-1] - 1) if len(bench) else None,
    }


def exit_alerts(s: Session, view: views.PortfolioView) -> list[str]:
    """Holdings whose tagged strategy no longer has them in its latest target."""
    out = []
    act = strat_store.active(s)
    active_cfg = must_get(s, StrategyConfigRow, act.config_id) if act else None
    for sym, row in view.positions.iterrows():
        for tag in [t for t in str(row.strategies).split(",") if t]:
            cfg: StrategyConfigRow | None
            if active_cfg is not None and active_cfg.strategy_name == tag:
                cfg = active_cfg
            else:
                cfg = strat_store.find_config(s, tag)
            if cfg is None:
                continue
            run = daily_store.signal_run(s, cfg.id)
            if run and sym not in daily_store.targets(s, run.date, cfg.id):
                out.append(f"{sym}: {tag} no longer holds it (target of {run.date})")
    return out


@router.get("/today")
def today(s: Session = Depends(db)) -> dict:
    view = views.portfolio(s)
    last_data, expected = data_last_date(), expected_last_session()
    alerts: list[str] = []
    stale = last_data is None or last_data < expected.isoformat()
    if stale:
        alerts.append(
            f"Prices end {last_data}, but {expected} has closed: run the daily job."
        )
    jobs = runs.jobs(s, "daily", 1)
    if jobs and jobs[0].status == "failed":
        alerts.append("The last daily job failed; see Settings and jobs for its log.")
    if len(view.snapshots) and view.snapshots.cash.iloc[-1] < -0.005:
        alerts.append("Cash is negative: record the deposit that funded your buys.")
    if view.missing_prices:
        alerts.append(
            "No market price yet for "
            + ", ".join(view.missing_prices)
            + " (valued at the last fill price until the daily job fetches it)."
        )
    alerts += exit_alerts(s, view)

    act = strat_store.active(s)
    active = None
    if act is not None:
        cfg = must_get(s, strat_store.StrategyConfigRow, act.config_id)
        run = daily_store.signal_run(s, cfg.id)
        active = {
            "name": cfg.strategy_name,
            "label": strat_store.label(cfg),
            "capital_usd": act.capital_usd,
            "is_rebalance": run.is_rebalance if run else None,
            "signal_date": iso(run.date) if run else None,
            "next_rebalance": iso(run.next_rebalance) if run else None,
        }
    sig_date = daily_store.latest_signal_date(s)
    sugg = daily_store.suggestions(s, sig_date) if sig_date else []
    return {
        "data_last_date": last_data,
        "expected_session": expected.isoformat(),
        "stale": stale,
        "totals": totals(view),
        "active": active,
        "signal_date": iso(sig_date),
        "suggestions": [row_dict(r, SUGGESTION_COLS) for r in sugg],
        "alerts": alerts,
        "last_job": (
            row_dict(jobs[0], ["id", "status", "session_date", "finished_at"])
            if jobs
            else None
        ),
    }


@router.get("/portfolio")
def portfolio(s: Session = Depends(db)) -> dict:
    view = views.portfolio(s)
    lots = [
        {
            "symbol": lot.symbol,
            "open_date": iso(lot.open_date),
            "qty": lot.qty,
            "cost_per_share": lot.cost_per_share,
            "strategy": lot.strategy,
        }
        for sym in view.book.open_symbols()
        for lot in view.book.lots[sym]
    ]
    realized = [
        {
            "date": iso(r.date),
            "symbol": r.symbol,
            "qty": r.qty,
            "proceeds": r.proceeds,
            "cost": r.cost,
            "pnl": r.pnl,
            "strategy": r.strategy,
        }
        for r in reversed(view.book.realized)
    ]
    return {
        "totals": totals(view),
        "positions": records(view.positions),
        "lots": lots,
        "realized": realized[:100],
        "missing_prices": view.missing_prices,
    }


@router.get("/portfolio/history")
def history(s: Session = Depends(db)) -> dict:
    snap = views.portfolio(s).snapshots
    dates = list(snap.index)
    bench = views.benchmark(dates)
    peak = snap.twr_index.cummax() if len(snap) else snap.twr_index
    return {
        "dates": [iso(d) for d in dates],
        "equity": [float(x) for x in snap.equity],
        "deposits": [float(x) for x in snap.deposits_cum],
        "day_pnl": [float(x) for x in snap.day_pnl],
        "twr": [float(x - 1) for x in snap.twr_index],
        "drawdown": [float(x) for x in (snap.twr_index / peak - 1)],
        "qqq": [None if pd.isna(x) else float(x - 1) for x in bench],
    }


# ---------- fills ----------
@router.get("/fills")
def list_fills(s: Session = Depends(db)) -> list[dict]:
    return [row_dict(r, FILL_COLS) for r in reversed(ledger.fills(s))]


@router.post("/fills", status_code=201)
def add_fill(body: FillIn, s: Session = Depends(db)) -> dict:
    row = ledger.add_fill(s, ledger.make_fill(**body.model_dump()))
    return row_dict(row, FILL_COLS)


@router.patch("/fills/{fill_id}")
def update_fill(fill_id: int, body: FillPatch, s: Session = Depends(db)) -> dict:
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    row = ledger.update_fill(s, fill_id, changes)
    if row.suggestion_id:
        daily_store.refresh_status(
            s, must_get(s, daily_store.SuggestionRow, row.suggestion_id)
        )
    return row_dict(row, FILL_COLS)


@router.delete("/fills/{fill_id}")
def delete_fill(fill_id: int, s: Session = Depends(db)) -> dict:
    row: FillRow = ledger.delete_fill(s, fill_id)
    if row.suggestion_id:
        daily_store.refresh_status(
            s, must_get(s, daily_store.SuggestionRow, row.suggestion_id)
        )
    return {"deleted": fill_id}


# ---------- cash ----------
@router.get("/cash")
def list_cash(s: Session = Depends(db)) -> list[dict]:
    return [row_dict(r, CASH_COLS) for r in reversed(ledger.cash_flows(s))]


@router.post("/cash", status_code=201)
def add_cash(body: CashIn, s: Session = Depends(db)) -> dict:
    row: CashFlowRow = ledger.add_cash(s, body.date, body.kind, body.amount, body.note)
    return row_dict(row, CASH_COLS)


@router.delete("/cash/{cash_id}")
def delete_cash(cash_id: int, s: Session = Depends(db)) -> dict:
    ledger.delete_cash(s, cash_id)
    return {"deleted": cash_id}


@router.get("/audit")
def audit(s: Session = Depends(db)) -> list[dict]:
    cols = ["id", "ts", "entity", "entity_id", "action", "before", "after"]
    return [row_dict(r, cols) for r in ledger.audit_log(s)]
