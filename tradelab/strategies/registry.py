"""The strategy catalogue: every strategy with its params, rebalance schedule, universe and score.

Backtests, live signals and dashboards all iterate ``REGISTRY``; add a ``StrategySpec`` here and the
new strategy shows up everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

from ..data.prices import NASDAQ100, Panel
from ..types import Indicator, Scores, Weights
from . import library as lib

ScoreUnit = Literal["return", "vol", "level"]  # signed %, unsigned %, plain number


@dataclass(frozen=True)
class StrategySpec:
    name: str
    label: str  # short display name
    fn: Callable[..., Weights]
    # the quantity the strategy ranks or thresholds on
    score: Callable[..., Scores | Indicator]
    score_label: str
    score_unit: ScoreUnit
    rebalance: str  # "D", "W", "M", "Q" (see engine.rebalance_dates)
    universe: str = NASDAQ100  # or a single symbol, e.g. "QQQ"
    params: dict = field(default_factory=dict)
    # False = can hold shorts (not allowed as the active strategy in v1)
    long_only: bool = True

    @property
    def description(self) -> str:
        return (self.fn.__doc__ or "").strip().split("\n")[0]

    def weights(self, panel: Panel) -> Weights:
        return self.fn(panel, **self.params)

    def signal(self, panel: Panel) -> Scores | Indicator:
        return self.score(panel, **self.params)


_SPECS = [
    StrategySpec(
        "momentum_12_1",
        "Momentum",
        lib.momentum,
        lib.momentum_score,
        "12-1 return",
        "return",
        "M",
    ),
    StrategySpec(
        "ls_momentum",
        "L/S mom",
        lib.long_short_momentum,
        lib.momentum_score,
        "12-1 return",
        "return",
        "M",
        long_only=False,
    ),
    StrategySpec(
        "trend_200d",
        "Trend 200d",
        lib.trend,
        lib.trend_score,
        "vs SMA200",
        "return",
        "W",
    ),
    StrategySpec(
        "low_vol", "Low vol", lib.low_vol, lib.volatility_score, "60d vol", "vol", "M"
    ),
    StrategySpec(
        "rsi2_reversion",
        "RSI2",
        lib.rsi_reversion,
        lib.rsi_score,
        "RSI 2",
        "level",
        "D",
    ),
    StrategySpec(
        "reversal_1w",
        "Reversal 1w",
        lib.reversal_weekly,
        lib.reversal_score,
        "5d return",
        "return",
        "W",
    ),
    StrategySpec(
        "qqq_50_200_trend",
        "QQQ trend",
        lib.index_trend,
        lib.crossover_score,
        "SMA50 vs SMA200",
        "return",
        "D",
        universe="QQQ",
    ),
]
REGISTRY: dict[str, StrategySpec] = {s.name: s for s in _SPECS}
