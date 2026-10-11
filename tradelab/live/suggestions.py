"""Suggested orders: the active strategy's target, in shares, minus what you hold for it.

Pure function over plain inputs (see ``suggest``); the daily job stores the result. Nothing here
places orders: these are the mechanical output of the strategy you activated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from ..data.prices import Panel
from ..strategies.registry import StrategySpec
from .signals import Decision

EPS = 1e-9


@dataclass(frozen=True)
class Order:
    symbol: str
    action: str  # BUY | SELL | ADD | TRIM
    side: str  # BUY | SELL
    qty: float
    ref_price: float
    target_weight: float
    held_qty: float
    reason: str

    @property
    def value(self) -> float:
        return self.qty * self.ref_price


@dataclass(frozen=True)
class SizingRules:
    capital_usd: float
    min_order_usd: float = 100.0
    fractional: bool = False


def _shares(value: float, price: float, fractional: bool) -> float:
    q = value / price
    return round(q, 4) if fractional else float(math.floor(q + EPS))


def _fmt_score(score: float | None, unit: str) -> str:
    if score is None or math.isnan(score):
        return "n/a"
    if unit == "level":
        return f"{score:.1f}"
    sign = "" if unit == "vol" else ("+" if score >= 0 else "")
    return f"{sign}{score * 100:.1f}%"


def suggest(
    targets: dict[str, float],
    held: dict[str, float],
    prices: dict[str, float],
    scores: dict[str, float],
    rules: SizingRules,
    is_rebalance: bool,
    score_label: str,
    score_unit: str,
    cash_available: float | None = None,
) -> list[Order]:
    """Orders that move ``held`` (shares) to ``targets`` (weights of ``rules.capital_usd``).

    On a rebalance day every position is resized to target. On other days only membership
    changes are suggested (a target name you don't hold, or a holding no longer in the target),
    so a missed order or a mid-period activation still gets fixed without resizing on drift.
    Orders worth less than ``min_order_usd`` are skipped, except a full exit. When
    ``cash_available`` is given and buys would exceed it plus sell proceeds, every buy is
    scaled down by the same factor. Sells come first.
    """
    sells: list[Order] = []
    buys: list[tuple[str, float, float, float]] = (
        []
    )  # symbol, target value, price, weight
    for sym in sorted(set(targets) | set(held)):
        w = targets.get(sym, 0.0)
        q = held.get(sym, 0.0)
        if w <= EPS and q > EPS:  # leaves the target: always exit fully
            px = prices.get(sym)
            if px is None:
                continue
            why = f"leaves the target ({score_label} {_fmt_score(scores.get(sym), score_unit)})"
            sells.append(Order(sym, "SELL", "SELL", q, px, 0.0, q, why))
            continue
        if w <= EPS:
            continue
        if (
            not is_rebalance and q > EPS
        ):  # held and still targeted: no resizing off-schedule
            continue
        px = prices.get(sym)
        if px is None or px <= 0:
            continue
        buys.append((sym, w * rules.capital_usd, px, w))

    proceeds = sum(o.value for o in sells)
    orders: list[Order] = list(sells)
    want = []
    for sym, value, px, w in buys:
        q = held.get(sym, 0.0)
        delta = _shares(value, px, rules.fractional) - q
        if abs(delta) * px < rules.min_order_usd or abs(delta) < EPS:
            continue
        want.append((sym, delta, px, w, q))

    for sym, delta, px, w, q in [x for x in want if x[1] < 0]:  # trims are sells
        why = f"rebalance to {w:.1%} of capital"
        orders.append(Order(sym, "TRIM", "SELL", -delta, px, w, q, why))
        proceeds += -delta * px
    adds = [x for x in want if x[1] > 0]
    need = sum(d * px for _, d, px, _, _ in adds)
    scale = 1.0
    if cash_available is not None and need > EPS:
        scale = min(1.0, max(0.0, cash_available + proceeds) / need)
    for sym, delta, px, w, q in adds:
        qty = _shares(delta * px * scale, px, rules.fractional)
        if qty * px < rules.min_order_usd or qty < EPS:
            continue
        if q > EPS:
            why = f"rebalance to {w:.1%} of capital"
            orders.append(Order(sym, "ADD", "BUY", qty, px, w, q, why))
        else:
            score = _fmt_score(scores.get(sym), score_unit)
            why = f"enters the target at {w:.1%} ({score_label} {score})"
            if scale < 1 - EPS:
                why += f"; scaled to {scale:.0%} for available cash"
            orders.append(Order(sym, "BUY", "BUY", qty, px, w, q, why))
    return orders


def for_strategy(
    spec: StrategySpec,
    panel: Panel,
    decision: Decision,
    held: dict[str, float],
    rules: SizingRules,
    cash_available: float | None,
    force_rebalance: bool = False,
) -> list[Order]:
    """``suggest`` for one strategy's decision, priced at that session's close."""
    day = pd.Timestamp(decision.date)
    close = panel.close.loc[day].dropna().astype(float).to_dict()
    score = spec.signal(panel).loc[day].dropna().astype(float).to_dict()
    targets = {h.symbol: h.weight for h in decision.holdings}
    names = set(targets) | set(held)
    return suggest(
        targets,
        held,
        {k: close[k] for k in names if k in close},
        {k: score[k] for k in names if k in score},
        rules,
        force_rebalance or decision.is_rebalance,
        spec.score_label,
        spec.score_unit,
        cash_available=cash_available,
    )
