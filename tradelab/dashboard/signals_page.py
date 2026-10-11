"""Daily signals + portfolio page. Decisions come from ``live.signals`` and positions from
``live.portfolio``; this module only shapes them for the template."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from ..data import market_calendar as cal
from ..data.prices import MARKET_TZ, NASDAQ100, load_constituents, short_name
from ..live import portfolio as pf
from ..live import signals
from ..strategies.registry import REGISTRY
from . import render

NAMES_EXTRA = {"QQQ": "Invesco QQQ Trust (Nasdaq-100 ETF)"}


def _day(d: signals.Decision) -> dict:
    return {
        "decision": d.is_rebalance,
        "since": str(d.target_since) if d.target_since else None,
        "next": str(d.next_rebalance),
        "hold": [[p.symbol, render.num(p.weight, 6), render.num(p.signal, 4), render.num(p.price, 2)] for p in d.holdings],
        "trades": [[t.symbol, t.action, render.num(t.before, 6), render.num(t.after, 6), render.num(t.signal, 4),
                    render.num(t.price, 2)] for t in d.trades],
    }


def portfolio_payload(ledger: Path | None = None) -> dict:
    led = pf.load_ledger(ledger)
    pos, realized = pf.positions(led)
    rows = pf.valued(pos).reset_index().to_dict("records") if len(pos) else []
    return {
        "ledger": str(ledger or pf.LEDGER),
        "realized": realized,
        "positions": [{k: render.plain(v) for k, v in r.items()} for r in rows],
        "trades": [{k: render.plain(v) for k, v in r.items()} for r in led.iloc[::-1].head(30).to_dict("records")],
    }


def build_payload(days: int = 63, ledger: Path | None = None, now: pd.Timestamp | None = None) -> dict:
    panels = signals.load_panels(REGISTRY.values())
    universe = panels[NASDAQ100]
    by_strategy = signals.latest(days, panels)
    as_of: dt.date = universe.close.index[-1].date()
    expected = signals.expected_last_session(now)
    cons = load_constituents()
    return {
        "meta": {
            "as_of": str(as_of), "next_session": str(cal.next_session(as_of)), "expected": str(expected),
            "stale": as_of < expected, "built": str(pd.Timestamp.now(tz=MARKET_TZ).strftime("%Y-%m-%d %H:%M %Z")),
            "universe": len(universe.symbols),
        },
        "dates": [str(d.date()) for d in universe.close.index[-days:]],
        "names": {**dict(zip(cons["symbol"], cons["name"].map(short_name))), **NAMES_EXTRA},
        "portfolio": portfolio_payload(ledger),
        "strategies": [
            {"name": name, "short": spec.label, "desc": spec.description, "rebalance": spec.rebalance,
             "unit": spec.score_unit, "score_label": spec.score_label,
             "days": {str(d.date): _day(d) for d in by_strategy[name]}}
            for name, spec in REGISTRY.items()
        ],
    }


def build(out: Path, days: int = 63, ledger: Path | None = None) -> dict:
    payload = build_payload(days, ledger)
    render.write("signals.html", payload, out)
    return payload
