"""Strategies, stored signals, the active strategy, and suggested orders."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..live import portfolio as pf
from ..store import daily as daily_store
from ..store import ledger, views
from ..store import strategies as strat_store
from ..store.models import StrategyConfigRow, SuggestionRow
from ..strategies.registry import REGISTRY
from ..store.db import must_get
from .deps import db, iso, row_dict
from .portfolio import FILL_COLS, SUGGESTION_COLS
from .schemas import SizingIn, SuggestionFillIn

router = APIRouter()


@router.get("/strategies")
def strategies() -> list[dict]:
    return [
        {
            "name": spec.name,
            "label": spec.label,
            "description": spec.description,
            "rebalance": spec.rebalance,
            "universe": spec.universe,
            "score_label": spec.score_label,
            "score_unit": spec.score_unit,
            "long_only": spec.long_only,
            "params": spec.params,
        }
        for spec in REGISTRY.values()
    ]


@router.get("/signals")
def signals(strategy: str, s: Session = Depends(db)) -> dict:
    """The latest stored target of a strategy at its default parameters."""
    cfg = strat_store.find_config(s, strategy)
    run = daily_store.signal_run(s, cfg.id) if cfg else None
    if cfg is None or run is None:
        raise HTTPException(404, f"no stored signals for {strategy}; run the daily job")
    targets = daily_store.target_rows(s, run.date, cfg.id)
    return {
        "strategy": strategy,
        "date": iso(run.date),
        "is_rebalance": run.is_rebalance,
        "next_rebalance": iso(run.next_rebalance),
        "targets": [
            {"symbol": t.symbol, "weight": t.weight, "score": t.score} for t in targets
        ],
    }


def active_dict(s: Session) -> dict | None:
    act = strat_store.active(s)
    if act is None:
        return None
    cfg = must_get(s, StrategyConfigRow, act.config_id)
    spec = strat_store.spec_for(cfg)
    return {
        "config_id": cfg.id,
        "name": cfg.strategy_name,
        "label": strat_store.label(cfg),
        "params": cfg.params,
        "description": spec.description,
        "rebalance": spec.rebalance,
        "score_label": spec.score_label,
        "score_unit": spec.score_unit,
        "capital_usd": act.capital_usd,
        "min_order_usd": act.min_order_usd,
        "fractional": act.fractional,
        "activated_at": iso(act.activated_at),
        "promoted_from_run": act.promoted_from_run,
        "code_sha": cfg.code_sha,
    }


@router.get("/active-strategy")
def get_active(s: Session = Depends(db)) -> dict | None:
    return active_dict(s)


@router.put("/active-strategy")
def update_active(body: SizingIn, s: Session = Depends(db)) -> dict | None:
    strat_store.update_sizing(s, body.capital_usd, body.min_order_usd, body.fractional)
    return active_dict(s)


@router.delete("/active-strategy")
def deactivate(s: Session = Depends(db)) -> dict:
    strat_store.deactivate(s)
    return {"active": None}


@router.get("/strategy")
def strategy_detail(s: Session = Depends(db)) -> dict:
    """Active strategy: target, schedule, paper vs real equity, adherence."""
    info = active_dict(s)
    if info is None:
        return {"active": None}
    run = daily_store.signal_run(s, info["config_id"])
    target = []
    if run is not None:
        view = views.portfolio(s)
        held = pf.strategy_holdings(view.book, info["name"])
        for t in daily_store.target_rows(s, run.date, info["config_id"]):
            target.append(
                {
                    "symbol": t.symbol,
                    "weight": t.weight,
                    "score": t.score,
                    "held": held.get(t.symbol, 0.0),
                }
            )
    since = dt.date.fromisoformat(info["activated_at"])
    paper = daily_store.paper(s, info["config_id"])
    real = views.portfolio(s).snapshots
    real = real[real.index >= since]
    sugg = [
        r
        for r in daily_store.suggestions(s)
        if r.config_id == info["config_id"] and r.signal_date >= since
    ]
    decided = [r for r in sugg if r.status != "open"]
    acted = [r for r in decided if r.status in ("done", "partial")]
    return {
        "active": info,
        "signal_date": iso(run.date) if run else None,
        "is_rebalance": run.is_rebalance if run else None,
        "next_rebalance": iso(run.next_rebalance) if run else None,
        "target": target,
        "paper": {
            "dates": [iso(d) for d in paper.index],
            "equity": [float(x) for x in paper],
        },
        "real": {
            "dates": [iso(d) for d in real.index],
            "equity": [float(x) for x in real.equity],
        },
        "adherence": {
            "acted": len(acted),
            "decided": len(decided),
            "share": len(acted) / len(decided) if decided else None,
        },
    }


# ---------- suggestions ----------
@router.get("/suggestions")
def list_suggestions(
    date: dt.date | None = None, s: Session = Depends(db)
) -> list[dict]:
    day = date or daily_store.latest_signal_date(s)
    rows = daily_store.suggestions(s, day) if day else []
    return [row_dict(r, SUGGESTION_COLS) for r in rows]


def _suggestion(s: Session, sid: int) -> SuggestionRow:
    return must_get(s, SuggestionRow, sid)


@router.post("/suggestions/{sid}/fill", status_code=201)
def fill_suggestion(sid: int, body: SuggestionFillIn, s: Session = Depends(db)) -> dict:
    sug = _suggestion(s, sid)
    cfg = must_get(s, StrategyConfigRow, sug.config_id)
    row = ledger.add_fill(
        s,
        ledger.make_fill(
            body.trade_date or sug.for_session,
            sug.side,
            sug.symbol,
            body.qty or sug.qty - sug.filled_qty,
            body.price,
            body.fees,
            cfg.strategy_name,
            body.note,
            suggestion_id=sug.id,
        ),
    )
    daily_store.refresh_status(s, sug)
    return {
        "fill": row_dict(row, FILL_COLS),
        "suggestion": row_dict(sug, SUGGESTION_COLS),
    }


@router.post("/suggestions/{sid}/dismiss")
def dismiss(sid: int, s: Session = Depends(db)) -> dict:
    sug = _suggestion(s, sid)
    if sug.status in ("open", "partial"):
        sug.status = "dismissed"
    return row_dict(sug, SUGGESTION_COLS)
