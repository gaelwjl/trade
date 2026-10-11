"""Run registered strategies and benchmarks, and parameter sweeps."""

from __future__ import annotations

import dataclasses
import itertools

import pandas as pd

from ..data.prices import NASDAQ100, Panel
from ..strategies.library import equal_weight
from ..strategies.registry import REGISTRY, StrategySpec
from ..types import Mask, StatsTable, Weights
from .engine import CostModel, Result, backtest

BENCHMARKS = {
    "EW universe": "Equal weight of every stock in the (survivor) universe, monthly rebalance. Shares the "
    "universe's survivorship bias, so it is the fair yardstick.",
    "QQQ": "Buy and hold the Nasdaq-100 ETF.",
}


def run(panel: Panel, spec: StrategySpec, **engine_kw) -> Result:
    return backtest(
        panel,
        spec.weights(panel),
        rebalance=spec.rebalance,
        name=spec.name,
        **engine_kw,
    )


def sweep(
    panel: Panel,
    spec: StrategySpec,
    grid: dict[str, list],
    benchmark: pd.Series | None = None,
    **engine_kw,
) -> StatsTable:
    """Grid-search params. Beware: picking the best row in-sample is overfitting -- check neighbours & OOS."""
    rows = []
    for combo in itertools.product(*grid.values()):
        params = dict(zip(grid, combo))
        variant = dataclasses.replace(spec, params={**spec.params, **params})
        try:
            res = run(panel, variant, **engine_kw)
        except (
            ValueError
        ) as e:  # e.g. "never took a position": keep the row so the gap is visible
            rows.append({**params, "error": str(e)})
            continue
        rows.append({**params, **res.stats(benchmark)})
    return StatsTable(pd.DataFrame(rows))


def universe_benchmark(panel: Panel, **engine_kw) -> Result:
    """Equal-weight, monthly-rebalanced buy-and-hold of the same (survivor) universe. The fair yardstick."""
    return backtest(
        panel,
        equal_weight(Mask(panel.close.notna())),
        rebalance="M",
        name="EW universe",
        **engine_kw,
    )


def etf_benchmark(symbol: str = "QQQ", **engine_kw) -> Result:
    p = Panel.load([symbol])
    return backtest(
        p,
        Weights(p.close.notna().astype(float)),
        rebalance="D",
        name=symbol,
        **engine_kw,
    )


def run_suite(
    panel: Panel,
    start: str | None = "2005-01-01",
    end: str | None = None,
    costs: CostModel = CostModel(),
    execution: str = "next_open",
    cash_rate: float = 0.0,
) -> dict[str, Result]:
    """Every registered strategy plus the two benchmarks. ``panel`` is the Nasdaq-100 universe."""
    kw = dict(
        start=start, end=end, costs=costs, execution=execution, cash_rate=cash_rate
    )
    panels = {NASDAQ100: panel}
    out = {}
    for name, spec in REGISTRY.items():
        if spec.universe not in panels:
            panels[spec.universe] = Panel.load_universe(spec.universe)
        out[name] = run(panels[spec.universe], spec, **kw)
    out["EW universe"] = universe_benchmark(panel, **kw)
    out["QQQ"] = etf_benchmark("QQQ", **kw)
    return out


def table(
    results: dict[str, Result], benchmark: str = "QQQ", rf: float = 0.0
) -> StatsTable:
    bench = results[benchmark].returns if benchmark in results else None
    return StatsTable(
        pd.DataFrame({k: r.stats(bench, rf) for k, r in results.items()}).T
    )
