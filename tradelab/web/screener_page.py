"""Screener / watchlist / technical-chart page. Indicators and screener fields come from the logic
layer (tested, causal); this module only shapes them for the template."""

from __future__ import annotations

from pathlib import Path

from ..data.prices import Panel
from ..live import screener
from ..strategies import indicators as ind
from ..types import Indicator, PriceFrame
from . import render


def build_payload(panel: Panel, years: int = 2) -> dict:
    n = int(years * 252)
    c = panel.close
    bbu, _, bbl, _ = ind.bollinger(c)
    macd, sig, hist = ind.macd(c)
    frames: dict[str, PriceFrame | Indicator] = {
        "o": panel.open,
        "h": panel.high,
        "l": panel.low,
        "c": c,
        "v": panel.volume,
        "s20": ind.sma(c, 20),
        "s50": ind.sma(c, 50),
        "s200": ind.sma(c, 200),
        "bbu": bbu,
        "bbl": bbl,
        "rsi": ind.rsi(c),
        "macd": macd,
        "sig": sig,
        "hist": hist,
    }
    table = screener.screen(panel)
    return {
        "asof": str(c.index[-1].date()),
        "dates": [d.strftime("%Y-%m-%d") for d in c.index[-n:]],
        "rows": [
            {k: render.plain(v) for k, v in r.items()}
            for r in table.reset_index().to_dict("records")
        ],
        "symbols": {
            sym: {
                k: render.series(f[sym].iloc[-n:].tolist(), 0 if k == "v" else 3)
                for k, f in frames.items()
            }
            for sym in table.index.astype(str)
        },
    }


def build(out: Path, years: int = 2) -> dict:
    payload = build_payload(
        Panel.load(dedupe=False), years
    )  # show every listed share class
    render.write("screener.html", payload, out)
    return payload
