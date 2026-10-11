"""Strategy-comparison page. Metrics come from ``backtest.analysis``; this module only shapes them."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from ..backtest import analysis
from ..backtest.engine import CostModel, Result
from ..backtest.runner import BENCHMARKS, etf_benchmark, run_suite, universe_benchmark
from ..data.prices import Panel
from ..store import runs as run_store
from ..store import strategies as strat_store
from ..store.models import BacktestResultRow, BacktestRunRow, StrategyConfigRow
from ..strategies.registry import REGISTRY
from ..store.db import must_get
from . import render

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _describe(name: str) -> str:
    return BENCHMARKS[name] if name in BENCHMARKS else REGISTRY[name].description


def _table(t: pd.DataFrame) -> dict[str, dict]:
    return {
        str(k): {c: render.plain(v) for c, v in row.items()} for k, row in t.iterrows()
    }


def build_payload(
    panel: Panel,
    start: str,
    split: str,
    cost_bps: list[float],
    base: CostModel,
    execution: str,
) -> dict:
    results: dict[str, Result] = run_suite(
        panel, start=start, costs=base, execution=execution
    )
    dates = pd.DatetimeIndex(
        sorted(set().union(*(r.equity.index for r in results.values())))
    )
    periods = analysis.standard_periods(dates[0], dates[-1], split)
    costs = analysis.cost_sensitivity(panel, cost_bps, base, results, start, execution)
    return {
        "meta": {
            "universe": len(panel.symbols),
            "first": str(dates[0].date()),
            "last": str(dates[-1].date()),
            "split": split,
            "cost_bps": base.one_way * 1e4,
            "execution": execution.replace("_", " "),
        },
        "series": [
            {
                "name": k,
                "kind": "benchmark" if k in BENCHMARKS else "strategy",
                "desc": _describe(k),
                "rebalance": str(r.meta["rebalance"]),
            }
            for k, r in results.items()
        ],
        "dates": [d.strftime("%Y-%m-%d") for d in dates],
        "returns": {
            k: render.series(r.returns.reindex(dates).tolist(), 6)
            for k, r in results.items()
        },
        "rolling": {
            k: {
                "ret": render.series(
                    analysis.rolling_return(r.equity).reindex(dates).tolist(), 4
                ),
                "sharpe": render.series(
                    analysis.rolling_sharpe(r.returns).reindex(dates).tolist(), 3
                ),
            }
            for k, r in results.items()
        },
        "periods": [p.__dict__ for p in periods],
        "stats": {p.key: _table(analysis.period_stats(results, p)) for p in periods},
        "annual": {
            k: {
                str(y): render.plain(v)
                for y, v in analysis.annual_returns(r.returns).items()
            }
            for k, r in results.items()
        },
        "costs": {f"{bps:g}": _table(t) for bps, t in costs.items()},
    }


def build(
    out: Path,
    start: str = "2005-01-01",
    split: str = "2018-01-01",
    cost_bps: list[float] | None = None,
    base: CostModel = CostModel(),
    execution: str = "next_open",
) -> dict:
    payload = build_payload(
        Panel.load(), start, split, cost_bps or [0, 5, 10, 20], base, execution
    )
    render.write("backtests.html", payload, out)
    return payload


def benchmarks(start: str, base: CostModel, execution: str) -> dict[str, Result]:
    kw = dict(start=start, costs=base, execution=execution)
    return {
        "EW universe": universe_benchmark(Panel.load(), **kw),
        "QQQ": etf_benchmark("QQQ", **kw),
    }


def compare_payload(
    s: "Session", run_ids: list[int], bench: dict[str, Result] | None = None
) -> dict:
    """The comparison page for stored runs (+ both benchmarks over the same window).

    Run metrics are the stored ones; only the benchmarks are recomputed (pass ``bench`` to
    reuse them). The first run sets the period boundaries, start date and cost model.
    """
    selected = (must_get(s, BacktestRunRow, i) for i in run_ids)
    rows = [r for r in selected if r.status == "done"]
    if not rows:
        raise KeyError("no finished runs selected")
    first = rows[0]
    base = CostModel(first.commission_bps, first.slippage_bps)
    bench = bench or benchmarks(str(first.start), base, first.execution)
    first_res = must_get(s, BacktestResultRow, first.id)
    periods = [analysis.Period(**p) for p in first_res.periods]

    equity: dict[str, pd.Series] = {}
    stats: dict[str, dict] = {}
    annual: dict[str, dict] = {}
    costs: dict[str, dict] = {}
    meta: list[dict] = []
    for r in rows:
        cfg = must_get(s, StrategyConfigRow, r.config_id)
        res = must_get(s, BacktestResultRow, r.id)
        name = f"#{r.id} {strat_store.label(cfg)}"
        equity[name] = run_store.curves(res)["equity"]
        stats[name] = res.stats_by_period
        annual[name] = res.annual
        costs[name] = res.cost_sensitivity
        spec = strat_store.spec_for(cfg)
        meta.append(
            {
                "name": name,
                "kind": "strategy",
                "desc": spec.description,
                "rebalance": spec.rebalance,
            }
        )
    for name, b in bench.items():
        equity[name] = b.equity
        stats[name] = {
            k: {c: render.plain(v) for c, v in st.items()}
            for k, st in analysis.run_period_stats(b, bench["QQQ"], periods).items()
        }
        annual[name] = {
            str(y): render.plain(v)
            for y, v in analysis.annual_returns(b.returns).items()
        }
        st = b.stats(bench["QQQ"].returns)
        costs[name] = {
            f"{base.one_way * 1e4:g}": {
                "CAGR": render.plain(st["CAGR"]),
                "Sharpe": render.plain(st["Sharpe"]),
            }
        }
        meta.append(
            {
                "name": name,
                "kind": "benchmark",
                "desc": _describe(name),
                "rebalance": str(b.meta["rebalance"]),
            }
        )

    dates = pd.DatetimeIndex(sorted(set().union(*(e.index for e in equity.values()))))
    levels = sorted({k for c in costs.values() for k in c}, key=float)
    return {
        "meta": {
            "universe": len(Panel.load().symbols),
            "first": str(dates[0].date()),
            "last": str(dates[-1].date()),
            "split": str(first.split),
            "cost_bps": base.one_way * 1e4,
            "execution": first.execution.replace("_", " "),
        },
        "series": meta,
        "dates": [d.strftime("%Y-%m-%d") for d in dates],
        "returns": {
            k: render.series(e.pct_change().reindex(dates).tolist(), 6)
            for k, e in equity.items()
        },
        "rolling": {
            k: {
                "ret": render.series(
                    analysis.rolling_return(e).reindex(dates).tolist(), 4
                ),
                "sharpe": render.series(
                    analysis.rolling_sharpe(e.pct_change()).reindex(dates).tolist(), 3
                ),
            }
            for k, e in equity.items()
        },
        "periods": [p.__dict__ for p in periods],
        "stats": {p.key: {k: stats[k].get(p.key, {}) for k in equity} for p in periods},
        "annual": annual,
        "costs": {lv: {k: costs[k].get(lv, {}) for k in equity} for lv in levels},
    }
