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
# one adjusted OHLCV field (open, close, volume, ...)
PriceFrame = NewType("PriceFrame", pd.DataFrame)
# simple per-bar returns
ReturnFrame = NewType("ReturnFrame", pd.DataFrame)
# a technical indicator computed from prices (SMA, RSI, ...)
Indicator = NewType("Indicator", pd.DataFrame)
# cross-sectional signal values to rank (momentum, volatility, ...)
Scores = NewType("Scores", pd.DataFrame)
# booleans: which symbols satisfy a condition on each date
Mask = NewType("Mask", pd.DataFrame)
# portfolio weights as a fraction of equity (negative = short)
Weights = NewType("Weights", pd.DataFrame)

# --- single symbol ---
# one symbol's OHLCV rows indexed by date
Bars = NewType("Bars", pd.DataFrame)

# --- one row per entity ---
# index members: symbol, name, market_cap, as_of
ConstituentTable = NewType("ConstituentTable", pd.DataFrame)
# screener snapshot, indexed by symbol
ScreenTable = NewType("ScreenTable", pd.DataFrame)
# performance stats, one row per strategy / param combo
StatsTable = NewType("StatsTable", pd.DataFrame)
# per-symbol data checks after a fetch, indexed by symbol
QualityTable = NewType("QualityTable", pd.DataFrame)

# --- your portfolio ---
# one row per open position, indexed by symbol
PositionTable = NewType("PositionTable", pd.DataFrame)
# one row per session: equity, cash, P&L parts, time-weighted return index
SnapshotTable = NewType("SnapshotTable", pd.DataFrame)
