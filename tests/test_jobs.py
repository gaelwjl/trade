import datetime as dt
import sqlite3

import pandas as pd
import pytest

from tradelab.data.prices import Panel
from tradelab.jobs import backup, daily, worker
from tradelab.store import daily as daily_store
from tradelab.store import ledger, runs
from tradelab.store import strategies as strat_store
from tradelab.store.db import open_db, session_scope
from tradelab.store.models import BacktestRunRow, PortfolioSnapshotRow, SignalRunRow
from tests.conftest import requires_data


def test_backup_and_restore_roundtrip(tmp_path):
    db = tmp_path / "live.sqlite"
    engine = open_db(db)
    with session_scope(engine) as s:
        ledger.add_cash(s, dt.date(2026, 1, 5), "deposit", 1234)
    engine.dispose()
    saved = backup.backup(db, tmp_path / "backups")
    with sqlite3.connect(db) as c:
        c.execute("DELETE FROM cash_flows")
    backup.restore(saved, db)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT amount FROM cash_flows").fetchall() == [(1234.0,)]
    assert list(tmp_path.glob("live.before-restore-*.sqlite"))


@requires_data
def test_daily_job_end_to_end_and_idempotent(engine):
    last = Panel.load(["QQQ"]).close.index[-1]
    now = pd.Timestamp(last.date()).tz_localize("America/New_York") + pd.Timedelta(
        hours=18
    )
    with session_scope(engine) as s:
        ledger.add_cash(s, dt.date(2026, 1, 5), "deposit", 100_000)
        ledger.add_fill(
            s,
            ledger.make_fill(
                dt.date(2026, 1, 6), "BUY", "AAPL", 10, 250, strategy="rsi2_reversion"
            ),
        )
        strat_store.activate(
            s, strat_store.config(s, "rsi2_reversion"), 50_000, on=dt.date(2026, 3, 2)
        )
    first = daily.run_daily(engine, fetch=False, now=now)
    assert first.ok, first.log
    with session_scope(engine) as s:
        n_targets = s.query(SignalRunRow).count()
        n_sugg = len(daily_store.suggestions(s))
        n_snap = s.query(PortfolioSnapshotRow).count()
        assert n_targets == 7 and n_snap > 100
        assert len(daily_store.paper(s, strat_store.active(s).config_id)) > 50
    again = daily.run_daily(engine, fetch=False, now=now)
    assert again.ok
    with session_scope(engine) as s:
        assert s.query(SignalRunRow).count() == n_targets
        assert len(daily_store.suggestions(s)) == n_sugg
        assert runs.jobs(s, "daily")[0].status == "done"


@requires_data
def test_stale_data_fails_the_job_without_touching_outputs(engine):
    future = pd.Timestamp("2099-01-02 18:00", tz="America/New_York")
    rep = daily.run_daily(engine, fetch=False, now=future)
    assert not rep.ok and any("StaleData" in line for line in rep.log)
    with session_scope(engine) as s:
        assert runs.jobs(s, "daily")[0].status == "failed"
        assert s.query(SignalRunRow).count() == 0


@requires_data
def test_worker_runs_a_queued_backtest(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(runs, "DATA_DIR", tmp_path)
    with session_scope(engine) as s:
        cfg = strat_store.config(s, "qqq_50_200_trend")
        runs.submit(
            s,
            [
                BacktestRunRow(
                    config_id=cfg.id,
                    start=dt.date(2010, 1, 1),
                    split=dt.date(2018, 1, 1),
                    commission_bps=1,
                    slippage_bps=4,
                    execution="next_open",
                    cost_levels=[5.0, 20.0],
                )
            ],
        )
    run_id = worker.work_once(engine)
    assert worker.work_once(engine) is None
    with session_scope(engine) as s:
        run = s.get(BacktestRunRow, run_id)
        assert run.status == "done", run.error
        res = runs.result(s, run_id)
        assert set(res.stats_by_period) == {"full", "is", "oos", "5y"}
        assert res.stats_by_period["full"]["CAGR"] == pytest.approx(0.12, abs=0.08)
        assert set(res.cost_sensitivity) == {"5", "20"} and run.data_fingerprint
        assert len(runs.curves(res)) > 2000
