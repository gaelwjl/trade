"""Scheduler process: the daily job at 16:30 ET on NYSE sessions, a nightly backup, and a
catch-up run at start-up if the machine was asleep when a session closed."""

from __future__ import annotations

import logging

import pandas as pd
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import Engine

from ..data import market_calendar as cal
from ..data.prices import MARKET_TZ
from ..live.signals import expected_last_session
from ..store import runs
from ..store.db import DB_PATH, session_scope
from . import backup, daily

log = logging.getLogger("tradelab.scheduler")


def needs_run(engine: Engine, now: pd.Timestamp | None = None) -> bool:
    """True when the last completed session has no successful daily run yet."""
    expected = expected_last_session(now)
    with session_scope(engine) as s:
        last = runs.last_success(s, "daily")
    return last is None or last.session_date is None or last.session_date < expected


def daily_if_session(engine: Engine) -> None:
    today = pd.Timestamp.now(tz=MARKET_TZ).date()
    if not cal.is_session(today):
        log.info("%s is not an NYSE session; skipping", today)
        return
    rep = daily.run_daily(engine)
    log.info("daily job %s: %s", "ok" if rep.ok else "FAILED", "; ".join(rep.log[-3:]))


def serve(engine: Engine) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if needs_run(engine):
        log.info("catching up a missed session")
        rep = daily.run_daily(engine)
        log.info("catch-up %s", "ok" if rep.ok else "FAILED")
    sched = BlockingScheduler(timezone=MARKET_TZ)
    sched.add_job(
        daily_if_session,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone=MARKET_TZ),
        args=[engine],
        id="daily",
        misfire_grace_time=6 * 3600,
        coalesce=True,
    )
    sched.add_job(
        lambda: log.info("backup -> %s", backup.backup(DB_PATH)),
        CronTrigger(hour=23, minute=0, timezone=MARKET_TZ),
        id="backup",
        misfire_grace_time=12 * 3600,
        coalesce=True,
    )
    log.info("scheduler running: daily 16:30 ET on sessions, backup 23:00 ET")
    sched.start()
