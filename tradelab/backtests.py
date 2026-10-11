"""Build an interactive strategy-comparison page (one offline HTML file).

    python -m tradelab.backtests [--start 2005-01-01] [--split 2018-01-01] [--costs 0,5,10,20] [--open]

All metrics are computed here (``Result.between`` + ``Result.stats``); the page only draws them.
"""

from __future__ import annotations

import argparse
import math
import webbrowser
from pathlib import Path

import numpy as np
import pandas as pd

from . import pages, runner
from .data import ROOT, Panel
from .engine import CostModel, Result
from .metrics import TRADING_DAYS
from .strategies import REGISTRY, index_trend

BENCHMARKS = {
    "EW universe": "Equal weight of every stock in the (survivor) universe, monthly rebalance. Shares the "
    "universe's survivorship bias, so it is the fair yardstick.",
    "QQQ": "Buy and hold the Nasdaq-100 ETF.",
}
STAT_KEYS = [
    "CAGR",
    "Vol",
    "Sharpe",
    "Sortino",
    "MaxDD",
    "Calmar",
    "Beta",
    "Alpha",
    "Turnover/yr",
    "CostDrag/yr",
    "AvgExposure",
]


def _arr(s: pd.Series, decimals: int = 6) -> list[float | None]:
    return [
        None if math.isnan(v) else round(v, decimals) for v in s.astype(float).tolist()
    ]


def describe(name: str) -> str:
    if name in BENCHMARKS:
        return BENCHMARKS[name]
    fn = REGISTRY[name]["fn"] if name in REGISTRY else index_trend
    return (fn.__doc__ or "").strip().split("\n")[0]


def periods(first: pd.Timestamp, last: pd.Timestamp, split: str) -> list[dict]:
    five = (last - pd.DateOffset(years=5)).normalize()
    return [
        {
            "key": "full",
            "label": f"Full {first.year}–{last.year}",
            "start": None,
            "end": None,
        },
        {
            "key": "is",
            "label": f"In-sample {first.year}–{pd.Timestamp(split).year - 1}",
            "start": None,
            "end": split,
        },
        {
            "key": "oos",
            "label": f"Out-of-sample {pd.Timestamp(split).year}–{last.year}",
            "start": split,
            "end": None,
        },
        {"key": "5y", "label": "Last 5 years", "start": str(five.date()), "end": None},
    ]


def period_stats(
    results: dict[str, Result], start: str | None, end: str | None
) -> dict[str, dict]:
    cut = {k: r.between(start, end) for k, r in results.items()}
    bench = cut["QQQ"].returns
    return {
        k: {s: pages.plain(v) for s, v in r.stats(bench).items() if s in STAT_KEYS}
        for k, r in cut.items()
    }


def annual(r: pd.Series) -> dict[str, object]:
    yearly = (1 + r).groupby(pd.DatetimeIndex(r.index).year).prod() - 1
    return {str(y): pages.plain(v) for y, v in yearly.items()}


def build_data(
    panel: Panel,
    start: str,
    split: str,
    cost_bps: list[float],
    base: CostModel,
    execution: str,
) -> dict:
    results = runner.run_suite(panel, start=start, costs=base, execution=execution)
    names = list(results)
    dates = pd.DatetimeIndex(
        sorted(set().union(*(r.equity.index for r in results.values())))
    )
    first, last = dates[0], dates[-1]

    rolling = {}
    for k, r in results.items():
        ret = r.returns.reindex(dates)
        eq = r.equity.reindex(dates)
        roll = ret.rolling(TRADING_DAYS, min_periods=TRADING_DAYS)
        rolling[k] = {
            "ret": _arr(eq / eq.shift(TRADING_DAYS) - 1, 4),
            "sharpe": _arr(roll.mean() / roll.std() * np.sqrt(TRADING_DAYS), 3),
        }

    cost_table: dict[str, dict] = {}
    for bps in cost_bps:
        alt = (
            results
            if math.isclose(bps, base.one_way * 1e4)
            else runner.run_suite(
                panel,
                start=start,
                costs=CostModel(
                    commission_bps=0.0, slippage_bps=bps, borrow_bps=base.borrow_bps
                ),
                execution=execution,
            )
        )
        bench = alt["QQQ"].returns
        cost_table[f"{bps:g}"] = {
            k: {
                "CAGR": pages.plain(s.get("CAGR")),
                "Sharpe": pages.plain(s.get("Sharpe")),
            }
            for k, s in ((k, r.stats(bench)) for k, r in alt.items())
        }

    ps = periods(first, last, split)
    return {
        "meta": {
            "universe": len(panel.symbols),
            "first": str(first.date()),
            "last": str(last.date()),
            "split": split,
            "cost_bps": base.one_way * 1e4,
            "execution": execution.replace("_", " "),
        },
        "series": [
            {
                "name": k,
                "kind": "benchmark" if k in BENCHMARKS else "strategy",
                "desc": describe(k),
                "rebalance": str(results[k].meta["rebalance"]),
            }
            for k in names
        ],
        "dates": [d.strftime("%Y-%m-%d") for d in dates],
        "returns": {k: _arr(r.returns.reindex(dates)) for k, r in results.items()},
        "rolling": rolling,
        "periods": ps,
        "stats": {p["key"]: period_stats(results, p["start"], p["end"]) for p in ps},
        "annual": {k: annual(r.returns) for k, r in results.items()},
        "costs": cost_table,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2005-01-01")
    ap.add_argument(
        "--split", default="2018-01-01", help="in-sample / out-of-sample boundary"
    )
    ap.add_argument(
        "--costs",
        default="0,5,10,20",
        help="one-way cost levels (bps) for the sensitivity table",
    )
    ap.add_argument("--commission-bps", type=float, default=1.0)
    ap.add_argument("--slippage-bps", type=float, default=4.0)
    ap.add_argument("--next-close", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "reports" / "backtests.html"))
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()
    base = CostModel(a.commission_bps, a.slippage_bps)
    data = build_data(
        Panel.load(),
        a.start,
        a.split,
        [float(x) for x in a.costs.split(",")],
        base,
        "next_close" if a.next_close else "next_open",
    )
    out = Path(a.out)
    pages.write(pages.render("backtests.html", data), out)
    print(
        f"{len(data['series'])} series, {data['meta']['first']} -> {data['meta']['last']} -> {out} "
        f"({out.stat().st_size / 1e6:.1f} MB)"
    )
    if a.open:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
