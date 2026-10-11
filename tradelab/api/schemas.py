"""Request bodies (responses are plain JSON dicts)."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class FillIn(BaseModel):
    trade_date: dt.date
    side: str
    symbol: str
    qty: float = Field(gt=0)
    price: float = Field(gt=0)
    fees: float = Field(default=0.0, ge=0)
    strategy: str = ""
    note: str = ""


class FillPatch(BaseModel):
    trade_date: dt.date | None = None
    side: str | None = None
    symbol: str | None = None
    qty: float | None = Field(default=None, gt=0)
    price: float | None = Field(default=None, gt=0)
    fees: float | None = Field(default=None, ge=0)
    strategy: str | None = None
    note: str | None = None


class CashIn(BaseModel):
    date: dt.date
    kind: str
    amount: float = Field(gt=0)
    note: str = ""


class SuggestionFillIn(BaseModel):
    price: float = Field(gt=0)
    qty: float | None = Field(default=None, gt=0)  # default: the suggested quantity
    fees: float = Field(default=0.0, ge=0)
    trade_date: dt.date | None = None  # default: the session the order was for
    note: str = ""


class SizingIn(BaseModel):
    capital_usd: float | None = Field(default=None, gt=0)
    min_order_usd: float | None = Field(default=None, ge=0)
    fractional: bool | None = None


class PromoteIn(BaseModel):
    capital_usd: float = Field(gt=0)
    min_order_usd: float = Field(default=100.0, ge=0)
    fractional: bool = False


class BacktestIn(BaseModel):
    strategy: str
    params: dict = Field(default_factory=dict)
    grid: dict[str, list] = Field(
        default_factory=dict
    )  # each combination becomes a run
    start: dt.date = dt.date(2007, 1, 1)
    end: dt.date | None = None
    split: dt.date = dt.date(2018, 1, 1)
    commission_bps: float = 1.0
    slippage_bps: float = 4.0
    execution: str = "next_open"
    cost_levels: list[float] = Field(default_factory=lambda: [0.0, 5.0, 10.0, 20.0])


class DailyIn(BaseModel):
    fetch: bool = True


class WatchlistIn(BaseModel):
    symbols: list[str]
