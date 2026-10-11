"""Batch backtests: submit, list, inspect, preview and promote."""

from __future__ import annotations

import itertools
import math

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..data.prices import Panel
from ..live import portfolio as pf
from ..live import signals
from ..live.suggestions import SizingRules, for_strategy
from ..store import ledger, runs
from ..store import strategies as strat_store
from ..store.models import BacktestRunRow, StrategyConfigRow
from ..strategies.registry import REGISTRY
from ..store.db import must_get
from .deps import db, iso, row_dict
from .schemas import BacktestIn, PromoteIn
from .strategy import active_dict

router = APIRouter()

RUN_COLS = ["id", "sweep_id", "start", "end", "split", "commission_bps", "slippage_bps"]
RUN_COLS += ["execution", "cost_levels", "data_as_of", "data_fingerprint", "code_sha"]
RUN_COLS += ["status", "error", "created_at", "started_at", "finished_at"]


def run_dict(s: Session, r: BacktestRunRow, with_result: bool = False) -> dict:
    cfg = must_get(s, StrategyConfigRow, r.config_id)
    out = row_dict(r, RUN_COLS)
    out.update(
        strategy=cfg.strategy_name, params=cfg.params, label=strat_store.label(cfg)
    )
    res = runs.result(s, r.id)
    out["long_only"] = REGISTRY[cfg.strategy_name].long_only
    if res is not None:
        full, oos = res.stats_by_period.get("full", {}), res.stats_by_period.get(
            "oos", {}
        )
        out["summary"] = {
            "CAGR": full.get("CAGR"),
            "Sharpe": full.get("Sharpe"),
            "MaxDD": full.get("MaxDD"),
            "oos_Sharpe": oos.get("Sharpe"),
            "CAGR_20bps": res.cost_sensitivity.get("20", {}).get("CAGR"),
        }
        if with_result:
            out["result"] = {
                "periods": res.periods,
                "stats_by_period": res.stats_by_period,
                "annual": res.annual,
                "cost_sensitivity": res.cost_sensitivity,
            }
    return out


@router.post("/backtests", status_code=201)
def submit(body: BacktestIn, s: Session = Depends(db)) -> list[dict]:
    if body.strategy not in REGISTRY:
        raise HTTPException(400, f"unknown strategy {body.strategy!r}")
    if body.execution not in ("next_open", "next_close"):
        raise HTTPException(400, "execution must be next_open or next_close")
    keys = list(body.grid)
    combos = list(itertools.product(*body.grid.values())) if keys else [()]
    if len(combos) > 200:
        raise HTTPException(400, f"grid has {len(combos)} points; the limit is 200")
    rows = []
    for combo in combos:
        cfg = strat_store.config(
            s, body.strategy, {**body.params, **dict(zip(keys, combo))}
        )
        rows.append(
            BacktestRunRow(
                config_id=cfg.id,
                start=body.start,
                end=body.end,
                split=body.split,
                commission_bps=body.commission_bps,
                slippage_bps=body.slippage_bps,
                execution=body.execution,
                cost_levels=body.cost_levels,
                code_sha=strat_store.code_version(),
            )
        )
    return [run_dict(s, r) for r in runs.submit(s, rows)]


@router.get("/backtests")
def list_runs(s: Session = Depends(db)) -> list[dict]:
    return [run_dict(s, r) for r in runs.runs(s)]


def _run(s: Session, run_id: int) -> BacktestRunRow:
    return must_get(s, BacktestRunRow, run_id)


@router.get("/backtests/{run_id}")
def get_run(run_id: int, s: Session = Depends(db)) -> dict:
    return run_dict(s, _run(s, run_id), with_result=True)


@router.get("/backtests/{run_id}/preview")
def preview(
    run_id: int,
    capital_usd: float,
    min_order_usd: float = 100.0,
    fractional: bool = False,
    s: Session = Depends(db),
) -> dict:
    """What promoting this run would hold today and the orders that would follow."""
    cfg = must_get(s, StrategyConfigRow, _run(s, run_id).config_id)
    spec = strat_store.spec_for(cfg)
    panel = Panel.load_universe(spec.universe)
    d = signals.decisions(spec, panel, 1)[-1]
    fills, flows, actions = ledger.records(s)
    book = pf.replay(fills, flows, actions)
    orders = for_strategy(
        spec,
        panel,
        d,
        pf.strategy_holdings(book, spec.name),
        SizingRules(capital_usd, min_order_usd, fractional),
        cash_available=book.cash,
        force_rebalance=True,  # promoting = rebalance to the current target
    )
    return {
        "label": strat_store.label(cfg),
        "long_only": spec.long_only,
        "signal_date": iso(d.date),
        "next_rebalance": iso(d.next_rebalance),
        "target": [
            {
                "symbol": h.symbol,
                "weight": h.weight,
                "score": None if math.isnan(h.signal) else h.signal,
            }
            for h in sorted(d.holdings, key=lambda h: -h.weight)
        ],
        "orders": [o.__dict__ for o in orders],
        "cash_available": book.cash,
    }


@router.post("/backtests/{run_id}/promote")
def promote(run_id: int, body: PromoteIn, s: Session = Depends(db)) -> dict:
    run = _run(s, run_id)
    if run.status != "done":
        raise HTTPException(400, "only a finished run can be promoted")
    cfg = must_get(s, StrategyConfigRow, run.config_id)
    strat_store.activate(
        s, cfg, body.capital_usd, body.min_order_usd, body.fractional, run_id=run.id
    )
    return {"active": active_dict(s)}
