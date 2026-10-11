"""Health, jobs, watchlist and price series."""

from __future__ import annotations

import threading

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..data.prices import price_path
from ..jobs import daily
from ..live.signals import expected_last_session
from ..store import daily as daily_store
from ..store import runs
from .deps import db, iso, row_dict
from .portfolio import data_last_date
from .schemas import DailyIn, WatchlistIn

router = APIRouter()
_daily_lock = threading.Lock()

JOB_COLS = [
    "id",
    "kind",
    "session_date",
    "params",
    "status",
    "started_at",
    "finished_at",
]


@router.get("/health")
def health(s: Session = Depends(db)) -> dict:
    last = runs.last_success(s, "daily")
    return {
        "ok": True,
        "data_last_date": data_last_date(),
        "expected_session": expected_last_session().isoformat(),
        "last_daily": row_dict(last, JOB_COLS) if last else None,
        "daily_running": _daily_lock.locked(),
    }


@router.get("/jobs")
def jobs(kind: str | None = None, s: Session = Depends(db)) -> list[dict]:
    return [row_dict(j, [*JOB_COLS, "log_tail"]) for j in runs.jobs(s, kind)]


@router.post("/jobs/daily", status_code=202)
def run_daily(body: DailyIn, request: Request) -> dict:
    """Start the daily job in the background (one at a time)."""
    if not _daily_lock.acquire(blocking=False):
        raise HTTPException(409, "the daily job is already running")
    engine = request.app.state.engine

    def work() -> None:
        try:
            daily.run_daily(engine, fetch=body.fetch)
        finally:
            _daily_lock.release()

    threading.Thread(target=work, daemon=True, name="daily-job").start()
    return {"started": True, "fetch": body.fetch}


@router.get("/watchlist")
def get_watchlist(s: Session = Depends(db)) -> list[str]:
    return daily_store.watchlist(s)


@router.put("/watchlist")
def put_watchlist(body: WatchlistIn, s: Session = Depends(db)) -> list[str]:
    return daily_store.set_watchlist(s, body.symbols)


@router.get("/prices/{symbol}")
def prices(symbol: str, days: int = 252) -> dict:
    path = price_path(symbol.upper())
    if not path.exists():
        raise HTTPException(404, f"no cached prices for {symbol}")
    df = pd.read_parquet(path).iloc[-days:]
    return {
        "symbol": symbol.upper(),
        "dates": [iso(d.date()) for d in df.index],
        **{
            c.lower(): [float(x) for x in df[c]]
            for c in ("Open", "High", "Low", "Close")
        },
    }
