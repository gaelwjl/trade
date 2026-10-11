"""Your actual positions: a trade ledger (CSV) of fills, valued at the latest cached close.

Positions use average cost; realized P&L is booked on sells. The ledger lives in data/ (git-ignored).
Every function takes an optional ledger ``path``; ``None`` means the default ``LEDGER``.
"""
from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import pandas as pd

from ..data.prices import DATA_DIR, MARKET_TZ, price_path
from ..strategies.registry import REGISTRY
from ..types import PositionTable, TradeLedger

LEDGER = DATA_DIR / "portfolio" / "trades.csv"
COLUMNS = ["date", "side", "symbol", "qty", "price", "fees", "strategy", "note"]
STRATEGY_TAGS = [*REGISTRY, "manual"]


def load_ledger(path: Path | None = None) -> TradeLedger:
    path = path or LEDGER
    if not path.exists():
        return TradeLedger(pd.DataFrame(columns=COLUMNS))
    df = pd.read_csv(path, dtype={"symbol": str, "strategy": str, "note": str}, keep_default_na=False)
    for c in ("qty", "price", "fees"):
        df[c] = pd.to_numeric(df[c])
    return TradeLedger(df[COLUMNS])


def positions(ledger: TradeLedger) -> tuple[PositionTable, float]:
    """Open positions (average cost) and total realized P&L. Raises if a sell exceeds the holding."""
    book: dict[str, dict] = {}
    realized = 0.0
    led = ledger.sort_values("date", kind="stable")
    text = {c: led[c].astype(str).tolist() for c in ("date", "side", "symbol", "strategy")}
    nums = {c: led[c].to_numpy(dtype=float) for c in ("qty", "price", "fees")}
    for i in range(len(led)):
        date, side, sym, strat = text["date"][i], text["side"][i], text["symbol"][i], text["strategy"][i]
        qty, price, fees = float(nums["qty"][i]), float(nums["price"][i]), float(nums["fees"][i])
        p = book.setdefault(sym, {"qty": 0.0, "cost": 0.0, "opened": date, "strategies": []})
        if side == "BUY":
            if p["qty"] == 0:
                p.update(opened=date, strategies=[])
            p["qty"] += qty
            p["cost"] += qty * price + fees
            if strat and strat not in p["strategies"]:
                p["strategies"].append(strat)
        else:
            if qty > p["qty"] + 1e-9:
                raise ValueError(f"{date}: selling {qty:g} {sym} but only {p['qty']:g} held")
            avg = p["cost"] / p["qty"]
            realized += qty * (price - avg) - fees
            p["cost"] -= avg * qty
            p["qty"] -= qty
    rows = [{"symbol": s, "qty": p["qty"], "avg_cost": p["cost"] / p["qty"], "cost": p["cost"],
             "opened": p["opened"], "strategies": ",".join(p["strategies"])}
            for s, p in book.items() if p["qty"] > 1e-9]
    cols = ["qty", "avg_cost", "cost", "opened", "strategies"]
    table = pd.DataFrame(rows, columns=["symbol", *cols]).set_index("symbol")
    return PositionTable(table), realized


def last_close(symbol: str) -> tuple[float | None, str | None]:
    """Latest cached close and its date for any fetched symbol."""
    path = price_path(symbol)
    if not path.exists():
        return None, None
    c = pd.read_parquet(path, columns=["Close"])["Close"].dropna()
    return (float(c.iloc[-1]), str(c.index[-1].date())) if len(c) else (None, None)


def valued(pos: PositionTable) -> PositionTable:
    out = pos.copy()
    closes = {s: last_close(str(s)) for s in out.index}
    out["last"] = [closes[s][0] for s in out.index]
    out["last_date"] = [closes[s][1] for s in out.index]
    out["value"] = out["qty"] * out["last"]
    out["pnl"] = out["value"] - out["cost"]
    out["pnl_pct"] = out["pnl"] / out["cost"]
    return PositionTable(out)


def make_trade(side: str, symbol: str, qty: float, price: float, date: str | None = None,
               fees: float = 0.0, strategy: str = "", note: str = "") -> dict:
    side, symbol = side.upper(), symbol.upper().strip()
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")
    if not (qty > 0 and math.isfinite(qty)) or not (price > 0 and math.isfinite(price)) or fees < 0:
        raise ValueError("qty and price must be positive, fees non-negative")
    if strategy and strategy not in STRATEGY_TAGS:
        raise ValueError(f"unknown strategy {strategy!r}; use one of {STRATEGY_TAGS}")
    day = dt.date.fromisoformat(date) if date else pd.Timestamp.now(tz=MARKET_TZ).date()
    if day > pd.Timestamp.now(tz=MARKET_TZ).date():
        raise ValueError("trade date is in the future")
    return {"date": str(day), "side": side, "symbol": symbol, "qty": qty, "price": price, "fees": fees,
            "strategy": strategy, "note": note}


def append_trade(trade: dict, path: Path | None = None) -> None:
    """Validate against the existing ledger (no overselling), then append."""
    path = path or LEDGER
    ledger = load_ledger(path)
    positions(TradeLedger(pd.concat([ledger, pd.DataFrame([trade])], ignore_index=True)))  # raises if invalid
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([trade], columns=COLUMNS).to_csv(path, mode="a", header=not path.exists(), index=False)


def undo_last(path: Path | None = None) -> dict | None:
    path = path or LEDGER
    ledger = load_ledger(path)
    if not len(ledger):
        return None
    ledger.iloc[:-1].to_csv(path, index=False)
    return ledger.iloc[-1].to_dict()
