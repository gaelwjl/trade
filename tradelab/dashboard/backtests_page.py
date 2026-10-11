"""Strategy-comparison page. Metrics come from ``backtest.analysis``; this module only shapes them."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..backtest import analysis
from ..backtest.engine import CostModel, Result
from ..backtest.runner import BENCHMARKS, run_suite
from ..data.prices import Panel
from ..strategies.registry import REGISTRY
from . import render


def _describe(name: str) -> str:
    return BENCHMARKS[name] if name in BENCHMARKS else REGISTRY[name].description


def _table(t: pd.DataFrame) -> dict[str, dict]:
    return {str(k): {c: render.plain(v) for c, v in row.items()} for k, row in t.iterrows()}


def build_payload(panel: Panel, start: str, split: str, cost_bps: list[float], base: CostModel, execution: str) -> dict:
    results: dict[str, Result] = run_suite(panel, start=start, costs=base, execution=execution)
    dates = pd.DatetimeIndex(sorted(set().union(*(r.equity.index for r in results.values()))))
    periods = analysis.standard_periods(dates[0], dates[-1], split)
    costs = analysis.cost_sensitivity(panel, cost_bps, base, results, start, execution)
    return {
        "meta": {"universe": len(panel.symbols), "first": str(dates[0].date()), "last": str(dates[-1].date()),
                 "split": split, "cost_bps": base.one_way * 1e4, "execution": execution.replace("_", " ")},
        "series": [{"name": k, "kind": "benchmark" if k in BENCHMARKS else "strategy", "desc": _describe(k),
                    "rebalance": str(r.meta["rebalance"])} for k, r in results.items()],
        "dates": [d.strftime("%Y-%m-%d") for d in dates],
        "returns": {k: render.series(r.returns.reindex(dates).tolist(), 6) for k, r in results.items()},
        "rolling": {k: {"ret": render.series(analysis.rolling_return(r).reindex(dates).tolist(), 4),
                        "sharpe": render.series(analysis.rolling_sharpe(r).reindex(dates).tolist(), 3)}
                    for k, r in results.items()},
        "periods": [p.__dict__ for p in periods],
        "stats": {p.key: _table(analysis.period_stats(results, p)) for p in periods},
        "annual": {k: {str(y): render.plain(v) for y, v in analysis.annual_returns(r.returns).items()} for k, r in results.items()},
        "costs": {f"{bps:g}": _table(t) for bps, t in costs.items()},
    }


def build(out: Path, start: str = "2005-01-01", split: str = "2018-01-01", cost_bps: list[float] | None = None,
          base: CostModel = CostModel(), execution: str = "next_open") -> dict:
    payload = build_payload(Panel.load(), start, split, cost_bps or [0, 5, 10, 20], base, execution)
    render.write("backtests.html", payload, out)
    return payload
