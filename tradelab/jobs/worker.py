"""Backtest worker: drains the queue of submitted runs (design: Batch backtests)."""

from __future__ import annotations

import hashlib
import math
import time
import traceback

import pandas as pd
from sqlalchemy import Engine

from ..backtest import analysis, runner
from ..backtest.engine import CostModel, Result
from ..data.prices import Panel
from ..store import runs
from ..store import strategies as strat_store
from ..store.db import session_scope, must_get
from ..store.models import BacktestResultRow, BacktestRunRow
from ..web.render import plain


def fingerprint(panel: Panel) -> str:
    """Hash of the symbols, row counts and last dates the run saw."""
    c = panel.close
    parts = [
        f"{s}:{c[s].count()}:{c[s].last_valid_index()!s}" for s in sorted(c.columns)
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _clean(d: dict) -> dict:
    return {k: plain(v) for k, v in d.items()}


def execute(run: BacktestRunRow, spec_params: tuple[str, dict]) -> BacktestResultRow:
    """Run one backtest and its analytics; returns the row to store."""
    name, params = spec_params
    spec = strat_store.spec_for(
        strat_store.StrategyConfigRow(strategy_name=name, params=params)
    )
    panel = Panel.load_universe(spec.universe)
    kw = dict(
        start=str(run.start),
        end=str(run.end) if run.end else None,
        execution=run.execution,
    )
    base = CostModel(run.commission_bps, run.slippage_bps)
    res = runner.run(panel, spec, costs=base, **kw)
    bench = runner.etf_benchmark("QQQ", costs=base, **kw)
    periods = analysis.standard_periods(
        res.equity.index[0], res.equity.index[-1], str(run.split)
    )
    stats = analysis.run_period_stats(res, bench, periods)

    costs: dict[str, dict] = {}
    for bps in run.cost_levels:
        alt: Result = res
        if not math.isclose(bps, base.one_way * 1e4):
            alt = runner.run(
                panel, spec, costs=CostModel(0.0, bps, base.borrow_bps), **kw
            )
        st = alt.stats(bench.returns)
        costs[f"{bps:g}"] = _clean({"CAGR": st.get("CAGR"), "Sharpe": st.get("Sharpe")})

    path = runs.save_curves(
        run.id,
        pd.DataFrame(
            {
                "equity": res.equity,
                "turnover": res.turnover,
                "costs": res.costs,
                "exposure": res.exposure,
            }
        ),
    )
    run.data_as_of = panel.close.index[-1].date()
    run.data_fingerprint = fingerprint(panel)
    return BacktestResultRow(
        run_id=run.id,
        stats_by_period={k: _clean(v) for k, v in stats.items()},
        periods=[p.__dict__ for p in periods],
        annual={
            str(y): plain(v) for y, v in analysis.annual_returns(res.returns).items()
        },
        cost_sensitivity=costs,
        equity_path=path,
    )


def work_once(engine: Engine) -> int | None:
    """Process one queued run; returns its id, or None if the queue was empty."""
    with session_scope(engine) as s:
        run = runs.claim_next(s)
        if run is None:
            return None
        cfg = must_get(s, strat_store.StrategyConfigRow, run.config_id)
        spec_params = (cfg.strategy_name, dict(cfg.params))
        run_id = run.id
        job_id = runs.start_job(s, "backtest", params={"run_id": run_id}).id
    error, result = "", None
    with session_scope(engine) as s:
        run = must_get(s, BacktestRunRow, run_id)
        try:
            result = execute(run, spec_params)
        except Exception:
            error = traceback.format_exc(limit=8)
        runs.finish(s, run, result, error)
        runs.finish_job(
            s,
            must_get(s, runs.JobRunRow, job_id),
            result is not None,
            error or f"run {run_id} done",
        )
    return run_id


def work_forever(engine: Engine, poll_seconds: float = 5.0) -> None:
    with session_scope(engine) as s:
        runs.requeue_stale(s)
    while True:
        if work_once(engine) is None:
            time.sleep(poll_seconds)
