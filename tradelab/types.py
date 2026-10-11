"""Named frame types.

Each is a pandas DataFrame at runtime (``NewType`` costs nothing); the name says what the frame
means so signatures are self-documenting and a type checker rejects, say, scores passed as weights.
Wrap a frame to assert its meaning: ``Weights(df)``.

"Wide" frames are indexed by trading date with one column per symbol.
"""

from __future__ import annotations

from typing import NewType

import pandas as pd

# --- wide (date x symbol) ---
PriceFrame = NewType(
    "PriceFrame", pd.DataFrame
)  # one adjusted OHLCV field (open, close, volume, ...)
ReturnFrame = NewType("ReturnFrame", pd.DataFrame)  # simple per-bar returns
Indicator = NewType(
    "Indicator", pd.DataFrame
)  # a technical indicator computed from prices (SMA, RSI, ...)
Scores = NewType(
    "Scores", pd.DataFrame
)  # cross-sectional signal values to rank (momentum, volatility, ...)
Mask = NewType(
    "Mask", pd.DataFrame
)  # booleans: which symbols satisfy a condition on each date
Weights = NewType(
    "Weights", pd.DataFrame
)  # portfolio weights as a fraction of equity (negative = short)

# --- single symbol ---
Bars = NewType("Bars", pd.DataFrame)  # one symbol's OHLCV rows indexed by date

# --- one row per entity ---
ConstituentTable = NewType(
    "ConstituentTable", pd.DataFrame
)  # index members: symbol, name, market_cap, as_of
ScreenTable = NewType(
    "ScreenTable", pd.DataFrame
)  # screener snapshot, indexed by symbol
StatsTable = NewType(
    "StatsTable", pd.DataFrame
)  # performance stats, one row per strategy / param combo

# --- your own trading ---
TradeLedger = NewType("TradeLedger", pd.DataFrame)  # one row per fill: date, side, symbol, qty, price, fees, strategy, note
PositionTable = NewType("PositionTable", pd.DataFrame)  # one row per open position, indexed by symbol
QualityTable = NewType("QualityTable", pd.DataFrame)  # per-symbol data checks after a fetch, indexed by symbol
