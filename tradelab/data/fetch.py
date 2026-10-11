"""Refresh the cache: Nasdaq-100 constituents + full adjusted price history from Yahoo Finance."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..types import Bars, PriceFrame, QualityTable
from . import prices


@dataclass(frozen=True)
class FetchReport:
    constituents: int
    as_of: str
    requested: list[str]
    missing: list[str]
    quality: QualityTable  # first/last date, rows, interior gaps, largest daily move, zero-volume days


def refresh(extra_symbols: list[str] | None = None) -> FetchReport:
    """Download constituents, index/ETF history and ``extra_symbols`` (e.g. holdings outside the index)."""
    cons = prices.fetch_constituents()
    prices.DATA_DIR.mkdir(parents=True, exist_ok=True)
    cons.to_csv(prices.CONSTITUENTS_PATH, index=False)

    symbols = cons["symbol"].tolist() + prices.INDEX_SYMBOLS
    symbols += [s for s in (extra_symbols or []) if s not in symbols]
    frames = prices.download_prices(symbols)
    prices.save_prices(frames)
    return FetchReport(
        constituents=len(cons),
        as_of=str(cons["as_of"].iloc[0]),
        requested=symbols,
        missing=[s for s in symbols if s not in frames],
        quality=quality(frames),
    )


def quality(frames: dict[str, Bars]) -> QualityTable:
    stocks = PriceFrame(
        pd.DataFrame(
            {
                s: df["Close"]
                for s, df in frames.items()
                if s not in prices.INDEX_SYMBOLS
            }
        )
    )
    gaps = prices.interior_gaps(
        stocks
    ).sum()  # days the other stocks traded but this one has no bar
    rows = []
    for s, df in frames.items():
        r = df["Close"].pct_change(fill_method=None)
        rows.append(
            {
                "symbol": s,
                "first": df.index[0].date(),
                "last": df.index[-1].date(),
                "rows": len(df),
                "gaps": int(gaps.get(s, 0)),
                "max_abs_ret": r.abs().max(),
                "zero_vol_days": (
                    int((df["Volume"] == 0).sum()) if "Volume" in df else 0
                ),
            }
        )
    return QualityTable(pd.DataFrame(rows).set_index("symbol").sort_values("first"))
