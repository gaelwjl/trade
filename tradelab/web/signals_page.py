"""Daily signals + portfolio page. Decisions come from ``live.signals`` and positions from the
stored ledger (``store.views``); this module only shapes them for the template."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from ..data import market_calendar as cal
from ..data.prices import MARKET_TZ, NASDAQ100, load_constituents, short_name
from ..live import signals
from ..store import ledger, views
from ..store.db import DB_PATH
from ..strategies.registry import REGISTRY
from . import render

NAMES_EXTRA = {"QQQ": "Invesco QQQ Trust (Nasdaq-100 ETF)"}


def _day(d: signals.Decision) -> dict:
    return {
        "decision": d.is_rebalance,
        "since": str(d.target_since) if d.target_since else None,
        "next": str(d.next_rebalance),
        "hold": [
            [
                p.symbol,
                render.num(p.weight, 6),
                render.num(p.signal, 4),
                render.num(p.price, 2),
            ]
            for p in d.holdings
        ],
        "trades": [
            [
                t.symbol,
                t.action,
                render.num(t.before, 6),
                render.num(t.after, 6),
                render.num(t.signal, 4),
                render.num(t.price, 2),
            ]
            for t in d.trades
        ],
    }


def portfolio_payload(s: Session) -> dict:
    """Your positions (valued at the last raw close) and recent fills."""
    view = views.portfolio(s)
    positions = []
    for sym, r in view.positions.iterrows():
        positions.append(
            {
                "symbol": sym,
                **{
                    k: render.plain(r[k])
                    for k in ("qty", "avg_cost", "cost", "last", "value", "pnl")
                },
                "pnl_pct": render.plain(r["pnl_pct"]),
                "strategies": r["strategies"],
                "last_date": str(view.as_of) if view.as_of else None,
            }
        )
    trades = [
        {
            "date": str(f.trade_date),
            "side": f.side,
            "symbol": f.symbol,
            "qty": f.qty,
            "price": f.price,
            "fees": f.fees,
            "strategy": f.strategy,
            "note": f.note,
        }
        for f in reversed(ledger.fills(s))
    ][:30]
    return {
        "ledger": str(DB_PATH.relative_to(DB_PATH.parents[1])),
        "realized": view.book.realized_total,
        "positions": positions,
        "trades": trades,
    }


def strategies_payload(days: int = 63, now: pd.Timestamp | None = None) -> dict:
    """Every strategy's decisions for the last ``days`` sessions (heavy; cacheable)."""
    panels = signals.load_panels(REGISTRY.values())
    universe = panels[NASDAQ100]
    by_strategy = signals.latest(days, panels)
    as_of: dt.date = universe.close.index[-1].date()
    expected = signals.expected_last_session(now)
    cons = load_constituents()
    return {
        "meta": {
            "as_of": str(as_of),
            "next_session": str(cal.next_session(as_of)),
            "expected": str(expected),
            "stale": as_of < expected,
            "built": str(pd.Timestamp.now(tz=MARKET_TZ).strftime("%Y-%m-%d %H:%M %Z")),
            "universe": len(universe.symbols),
        },
        "dates": [str(d.date()) for d in universe.close.index[-days:]],
        "names": {
            **dict(zip(cons["symbol"], cons["name"].map(short_name))),
            **NAMES_EXTRA,
        },
        "strategies": [
            {
                "name": name,
                "short": spec.label,
                "desc": spec.description,
                "rebalance": spec.rebalance,
                "unit": spec.score_unit,
                "score_label": spec.score_label,
                "days": {str(d.date): _day(d) for d in by_strategy[name]},
            }
            for name, spec in REGISTRY.items()
        ],
    }


def build_payload(s: Session, days: int = 63, now: pd.Timestamp | None = None) -> dict:
    return {**strategies_payload(days, now), "portfolio": portfolio_payload(s)}


def build(out: Path, s: Session, days: int = 63) -> dict:
    payload = build_payload(s, days)
    render.write("signals.html", payload, out)
    return payload
