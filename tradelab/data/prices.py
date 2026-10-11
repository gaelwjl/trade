"""Nasdaq-100 constituents + Yahoo Finance price cache.

Prices are stored split- and dividend-adjusted (yfinance ``auto_adjust=True``), one parquet
file per symbol under ``data/prices``. Use ``Panel.load()`` to get wide date x symbol frames.
"""

from __future__ import annotations

import datetime as dt
import re
import warnings
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

from ..types import Bars, ConstituentTable, Mask, PriceFrame, ReturnFrame

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
PRICES_DIR = DATA_DIR / "prices"
CONSTITUENTS_PATH = DATA_DIR / "constituents.csv"

INDEX_SYMBOLS = ["^NDX", "^IXIC", "QQQ"]
NASDAQ100 = "nasdaq100"  # universe name for the current constituents
FIELDS = ["Open", "High", "Low", "Close", "Volume"]

MARKET_TZ = "America/New_York"
SESSION_DONE = dt.time(
    16, 15
)  # regular close is 16:00 ET; allow a few minutes for the final print

_SUFFIX = re.compile(
    r"\s+(Class [A-Z]\s+)?(Common Stock|Ordinary Shares?|American Depositary Shares?|Capital Stock|Common Shares?|Depositary Shares?).*$",
    re.I,
)

_NASDAQ_URL = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "application/json",
}


def fetch_constituents() -> ConstituentTable:
    """Current Nasdaq-100 members from Nasdaq's own API."""
    r = requests.get(_NASDAQ_URL, headers=_HEADERS, timeout=30)
    r.raise_for_status()
    payload = r.json()["data"]
    df = pd.DataFrame(payload["data"]["rows"])
    out = pd.DataFrame(
        {
            "symbol": df["symbol"].str.strip(),
            "name": df["companyName"],
            "market_cap": pd.to_numeric(
                df["marketCap"].str.replace(",", ""), errors="coerce"
            ),
            "as_of": payload.get("date"),
        }
    )
    return ConstituentTable(
        out.sort_values("market_cap", ascending=False).reset_index(drop=True)
    )


def load_constituents() -> ConstituentTable:
    return ConstituentTable(pd.read_csv(CONSTITUENTS_PATH))


def short_name(name: str) -> str:
    """Company name without the share-class / security-type suffix ("Alphabet Inc. Class C Capital Stock" -> "Alphabet Inc.")."""
    return _SUFFIX.sub("", str(name)).strip()


def one_per_company(cons: ConstituentTable) -> list[str]:
    """Symbols with extra share classes removed (GOOG/GOOGL -> keep the larger listing)."""
    c = cons.sort_values("market_cap", ascending=False)
    return c.loc[~c["name"].map(short_name).duplicated(keep="first"), "symbol"].tolist()


def drop_incomplete_bar(df: Bars, now: pd.Timestamp | None = None) -> Bars:
    """Drop today's bar while the US session is still open: Yahoo serves it as a live, partial bar."""
    now = now if now is not None else pd.Timestamp.now(tz=MARKET_TZ)
    if len(df) and df.index[-1].date() == now.date() and now.time() < SESSION_DONE:
        return Bars(df.iloc[:-1])
    return df


def price_path(symbol: str) -> Path:
    return PRICES_DIR / f"{symbol.replace('^', '_')}.parquet"


def _clean(df: Bars) -> Bars:
    df = df[[c for c in FIELDS if c in df.columns]].dropna(how="all", subset=["Close"])
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "Date"
    return Bars(df[~df.index.duplicated(keep="last")].sort_index())


def download_prices(symbols: list[str], batch_size: int = 25) -> dict[str, Bars]:
    """Full-history adjusted daily bars. Retries any symbol missing from the batch download."""
    out: dict[str, Bars] = {}
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i : i + batch_size]
        raw = yf.download(
            batch,
            period="max",
            interval="1d",
            auto_adjust=True,
            group_by="ticker",
            threads=True,
            progress=False,
        )
        for s in batch:
            try:
                df = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
                df = _clean(df)
            except KeyError:
                continue
            if len(df):
                out[s] = df
    for s in [s for s in symbols if s not in out]:
        df = _clean(yf.Ticker(s).history(period="max", interval="1d", auto_adjust=True))
        if len(df):
            out[s] = df
    return {s: drop_incomplete_bar(df) for s, df in out.items()}


@dataclass(frozen=True)
class RawHistory:
    """Unadjusted closes and corporate actions for one symbol (for portfolio valuation)."""

    close: pd.Series  # what the market actually quoted each day
    actions: list[tuple[dt.date, str, float]]  # (ex_date, "split" | "dividend", value)


def unadjust(bars: pd.DataFrame) -> RawHistory:
    """Undo Yahoo's split adjustment.

    With ``auto_adjust=False`` Yahoo's Close and dividend amounts are still restated in today's
    share count; multiplying back every split *after* a date gives the price quoted that day.
    """
    bars = bars.sort_index()
    splits = bars.get("Stock Splits", pd.Series(0.0, index=bars.index)).fillna(0.0)
    ratio = splits.where(splits > 0, 1.0)
    # product of split ratios strictly after each date
    later = ratio[::-1].cumprod()[::-1].shift(-1, fill_value=1.0)
    close = (bars["Close"] * later).dropna()
    split_days = splits[splits > 0]
    actions = [
        (d.date(), "split", float(r))
        for d, r in zip(pd.DatetimeIndex(split_days.index), split_days)
    ]
    divs = bars.get("Dividends", pd.Series(0.0, index=bars.index)).fillna(0.0)
    paid = divs[divs > 0]
    actions += [
        (d.date(), "dividend", float(v * f))
        for d, v, f in zip(
            pd.DatetimeIndex(paid.index), paid, later.reindex(paid.index)
        )
    ]
    return RawHistory(close=close, actions=sorted(actions))


def download_raw(symbols: list[str]) -> dict[str, RawHistory]:
    """Unadjusted closes + splits + dividends (full history) for ``symbols``."""
    out = {}
    for s in symbols:
        df = yf.Ticker(s).history(
            period="max", interval="1d", auto_adjust=False, actions=True
        )
        if not len(df):
            continue
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        df = drop_incomplete_bar(Bars(df[~df.index.duplicated(keep="last")]))
        out[s] = unadjust(df)
    return out


def interior_gaps(close: PriceFrame) -> Mask:
    """Missing bars between a symbol's first and last trade (not pre-listing / post-delisting)."""
    return Mask(close.isna() & close.ffill().notna() & close.bfill().notna())


def save_prices(frames: dict[str, Bars]) -> None:
    PRICES_DIR.mkdir(parents=True, exist_ok=True)
    for s, df in frames.items():
        df.to_parquet(price_path(s))


def _short_runs(gap: pd.Series, max_len: int) -> pd.Series:
    """True cells of ``gap`` that belong to a consecutive run of at most ``max_len``."""
    run_id = (~gap).cumsum()  # a gap run shares the id of the bar before it
    return gap & (gap.groupby(run_id).transform("sum") <= max_len)


@dataclass
class Panel:
    """Wide (date x symbol) adjusted OHLCV frames."""

    open: PriceFrame
    high: PriceFrame
    low: PriceFrame
    close: PriceFrame
    volume: PriceFrame
    filled: Mask | None = (
        None  # bars that were missing and forward-filled by ``from_frames``
    )

    @classmethod
    def load(
        cls, symbols: list[str] | None = None, dedupe: bool = True, max_gap: int = 5
    ) -> "Panel":
        """Load cached prices.

        Default universe: current Nasdaq-100 constituents, one share class per company when
        ``dedupe`` (otherwise GOOG and GOOGL count as two independent stocks). Interior gaps of up
        to ``max_gap`` bars are forward-filled (see ``from_frames``).
        """
        if symbols is None:
            cons = load_constituents()
            symbols = one_per_company(cons) if dedupe else cons["symbol"].tolist()
        cols: dict[str, dict[str, pd.Series]] = {f: {} for f in FIELDS}
        for s in symbols:
            path = price_path(s)
            if not path.exists():
                continue
            df = pd.read_parquet(path)
            for f in FIELDS:
                if f in df:
                    cols[f][s] = df[f]
        if not cols["Close"]:
            raise FileNotFoundError(
                "No cached prices found. Run: python -m tradelab fetch"
            )
        return cls.from_frames(
            {f: PriceFrame(pd.DataFrame(c)) for f, c in cols.items()}, max_gap
        )

    @classmethod
    def load_universe(cls, universe: str) -> "Panel":
        """``"nasdaq100"``: current constituents (one share class each); anything else: that one symbol."""
        return cls.load() if universe == NASDAQ100 else cls.load([universe])

    @classmethod
    def from_frames(cls, frames: dict[str, PriceFrame], max_gap: int = 5) -> "Panel":
        """Align fields on the union calendar and repair short interior gaps.

        A missing bar would otherwise poison every rolling indicator for a whole window (one NaN
        blanks a 200-day SMA for 200 days). Gaps of up to ``max_gap`` bars are filled as flat bars at
        the last close with zero volume and recorded in ``filled``; longer gaps stay NaN.
        """
        idx = frames["Close"].index.sort_values()
        f = {k: d.reindex(idx) for k, d in frames.items()}
        close = f["Close"]
        fill = Mask(
            interior_gaps(PriceFrame(close)).apply(lambda g: _short_runs(g, max_gap))
        )
        last = close.ffill()
        if fill.to_numpy().any():
            warnings.warn(
                f"forward-filled {int(fill.to_numpy().sum())} missing bars: "
                f"{fill.sum()[lambda s: s > 0].to_dict()}",
                stacklevel=2,
            )
        for k in ("Open", "High", "Low", "Close"):
            f[k] = f[k].mask(fill, last)
        f["Volume"] = f["Volume"].mask(fill, 0.0)
        return cls(
            open=PriceFrame(f["Open"]),
            high=PriceFrame(f["High"]),
            low=PriceFrame(f["Low"]),
            close=PriceFrame(f["Close"]),
            volume=PriceFrame(f["Volume"]),
            filled=fill,
        )

    @property
    def returns(self) -> ReturnFrame:
        return ReturnFrame(self.close.pct_change(fill_method=None))

    @property
    def symbols(self) -> list[str]:
        return list(self.close.columns)
