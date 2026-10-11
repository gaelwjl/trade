"""HTML pages served by the app. App pages fetch their data from /api; the signals, screener and
comparison pages carry their (heavier) data in the page, cached until the price files change.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from ..api.deps import db
from ..data.prices import CONSTITUENTS_PATH, Panel, PRICES_DIR
from ..store.db import must_get
from . import backtests_page, render, screener_page, signals_page

router = APIRouter(include_in_schema=False)
PLOTLY = "/static/plotly.js"
_cache: dict[tuple, Any] = {}


def data_stamp() -> float:
    """Changes whenever the daily job rewrites the price cache."""
    stamps = (
        [e.stat().st_mtime for e in os.scandir(PRICES_DIR)]
        if PRICES_DIR.exists()
        else []
    )
    if CONSTITUENTS_PATH.exists():
        stamps.append(CONSTITUENTS_PATH.stat().st_mtime)
    return max(stamps, default=0.0)


def cached(key: tuple, build: Callable[[], Any]) -> Any:
    full = (*key, data_stamp())
    if full not in _cache:
        for k in [k for k in _cache if k[: len(key)] == key]:
            del _cache[k]
        _cache[full] = build()
    return _cache[full]


def page(template: str, nav: str, payload: dict | None = None) -> HTMLResponse:
    return HTMLResponse(render.render(template, payload, nav=nav, plotly_src=PLOTLY))


@router.get("/static/plotly.js")
def plotly() -> Response:
    js = cached(("plotly",), render.plotly_js)
    return Response(
        js,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/")
def today() -> HTMLResponse:
    return page("today.html", "today")


@router.get("/portfolio")
def portfolio() -> HTMLResponse:
    return page("portfolio.html", "portfolio")


@router.get("/strategy")
def strategy() -> HTMLResponse:
    return page("strategy.html", "strategy")


@router.get("/backtests")
def backtests() -> HTMLResponse:
    return page("runs.html", "backtests")


@router.get("/backtests/compare")
def compare(ids: str, s: Session = Depends(db)) -> HTMLResponse:
    run_ids = [int(x) for x in ids.split(",") if x.strip().isdigit()][:6]
    first = must_get(s, backtests_page.BacktestRunRow, run_ids[0]) if run_ids else None
    if first is None:
        raise KeyError("no runs selected")
    base = backtests_page.CostModel(first.commission_bps, first.slippage_bps)
    bench = cached(
        (
            "bench",
            str(first.start),
            first.commission_bps,
            first.slippage_bps,
            first.execution,
        ),
        lambda: backtests_page.benchmarks(str(first.start), base, first.execution),
    )
    return page(
        "backtests.html", "backtests", backtests_page.compare_payload(s, run_ids, bench)
    )


@router.get("/signals")
def signals(s: Session = Depends(db)) -> HTMLResponse:
    core = cached(("signals",), signals_page.strategies_payload)
    return page(
        "signals.html",
        "signals",
        {**core, "portfolio": signals_page.portfolio_payload(s)},
    )


@router.get("/screener")
def screener() -> HTMLResponse:
    payload = cached(
        ("screener",), lambda: screener_page.build_payload(Panel.load(dedupe=False))
    )
    return page("screener.html", "screener", {**payload, "api": True})


@router.get("/settings")
def settings() -> HTMLResponse:
    return page("settings.html", "settings")
