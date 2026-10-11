"""Backtest runs (a queue the worker drains) and job history."""

from __future__ import annotations

import datetime as dt

import pandas as pd
from typing import cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.orm import Session

from ..data.prices import DATA_DIR
from ..types import RunCurves
from .models import BacktestResultRow, BacktestRunRow, JobRunRow, utcnow

RUNS_DIR = DATA_DIR / "runs"  # one parquet per run: equity, turnover, costs, exposure


def save_curves(run_id: int, curves: RunCurves) -> str:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"run_{run_id}.parquet"
    curves.to_parquet(path)
    return str(path.relative_to(DATA_DIR))


def curves(result: BacktestResultRow) -> RunCurves:
    return RunCurves(pd.read_parquet(DATA_DIR / result.equity_path))


# ---------- backtest runs ----------
MIN_PERIOD_DAYS = 90  # calendar days of data required on each side of the split


def check_dates(
    start: dt.date, split: dt.date, end: dt.date | None, data_last: dt.date | None
) -> None:
    """Reject runs whose in-sample or out-of-sample period would be (nearly) empty."""
    # without cached prices, today bounds the data: a future split never has out-of-sample data
    last = min(d for d in (end, data_last or dt.date.today()) if d is not None)
    if end is not None and end <= start:
        raise ValueError(f"end {end} must be after start {start}")
    if (split - start).days < MIN_PERIOD_DAYS:
        raise ValueError(
            f"split {split} must be at least {MIN_PERIOD_DAYS} days after start {start}"
        )
    if (last - split).days < MIN_PERIOD_DAYS:
        raise ValueError(
            f"split {split} leaves under {MIN_PERIOD_DAYS} days of out-of-sample data "
            f"(prices end {last}); pick a split before {last - dt.timedelta(days=MIN_PERIOD_DAYS)}"
        )


def submit(s: Session, rows: list[BacktestRunRow]) -> list[BacktestRunRow]:
    """Queue runs; several rows (a parameter grid) share one ``sweep_id``."""
    sweep = None
    if len(rows) > 1:
        sweep = (s.scalar(select(func.max(BacktestRunRow.sweep_id))) or 0) + 1
    for r in rows:
        r.status, r.sweep_id = "queued", sweep
    s.add_all(rows)
    s.flush()
    return rows


def claim_next(s: Session) -> BacktestRunRow | None:
    """Mark the oldest queued run as running and return it (None if the queue is empty)."""
    row = s.scalar(
        select(BacktestRunRow)
        .where(BacktestRunRow.status == "queued")
        .order_by(BacktestRunRow.id)
        .limit(1)
    )
    if row is None:
        return None
    claimed = cast(
        CursorResult,
        s.execute(
            update(BacktestRunRow)
            .where(BacktestRunRow.id == row.id, BacktestRunRow.status == "queued")
            .values(status="running", started_at=utcnow())
        ),
    )
    if claimed.rowcount != 1:  # another worker took it
        return None
    s.refresh(row)
    return row


def finish(
    s: Session, run: BacktestRunRow, result: BacktestResultRow | None, error: str = ""
) -> None:
    run.finished_at = utcnow()
    if result is None:
        run.status, run.error = "failed", error[-2000:]
        return
    s.merge(result)
    run.status, run.error = "done", ""


def requeue_stale(s: Session) -> int:
    """Runs left 'running' by a crashed worker go back to the queue."""
    res = cast(
        CursorResult,
        s.execute(
            update(BacktestRunRow)
            .where(BacktestRunRow.status == "running")
            .values(status="queued")
        ),
    )
    return res.rowcount or 0


def runs(s: Session, limit: int = 200) -> list[BacktestRunRow]:
    return list(
        s.scalars(
            select(BacktestRunRow).order_by(BacktestRunRow.id.desc()).limit(limit)
        )
    )


def result(s: Session, run_id: int) -> BacktestResultRow | None:
    return s.get(BacktestResultRow, run_id)


# ---------- jobs ----------
def start_job(
    s: Session,
    kind: str,
    session_date: dt.date | None = None,
    params: dict | None = None,
) -> JobRunRow:
    row = JobRunRow(
        kind=kind, session_date=session_date, params=params or {}, status="running"
    )
    s.add(row)
    s.flush()
    return row


def finish_job(s: Session, job: JobRunRow, ok: bool, log: str) -> None:
    job.status = "done" if ok else "failed"
    job.finished_at = utcnow()
    job.log_tail = log[-8000:]


def jobs(s: Session, kind: str | None = None, limit: int = 50) -> list[JobRunRow]:
    q = select(JobRunRow)
    if kind:
        q = q.where(JobRunRow.kind == kind)
    return list(s.scalars(q.order_by(JobRunRow.id.desc()).limit(limit)))


def last_success(s: Session, kind: str) -> JobRunRow | None:
    q = select(JobRunRow).where(JobRunRow.kind == kind, JobRunRow.status == "done")
    return s.scalar(
        q.order_by(JobRunRow.session_date.desc(), JobRunRow.id.desc()).limit(1)
    )
