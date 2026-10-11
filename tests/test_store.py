import datetime as dt

import pandas as pd
import pytest

from tradelab.data.prices import RawHistory
from tradelab.live.portfolio import LedgerError
from tradelab.live.suggestions import Order
from tradelab.store import daily as daily_store
from tradelab.store import ledger, market, runs
from tradelab.store import strategies as strat_store
from tradelab.store.db import session_scope
from tradelab.store.models import AuditRow, BacktestRunRow

D = dt.date


def test_ledger_validates_against_stored_splits(engine):
    with session_scope(engine) as s:
        idx = pd.to_datetime(["2026-01-05", "2026-01-06"])
        market.save_raw(
            s,
            "AAA",
            RawHistory(
                pd.Series([100.0, 50.0], index=idx), [(D(2026, 1, 6), "split", 2.0)]
            ),
        )
        ledger.add_fill(s, ledger.make_fill(D(2026, 1, 5), "buy", "aaa", 10, 100.0))
    with session_scope(engine) as s:
        # 20 shares after the 2:1 split: selling 20 is fine, 21 is not
        with pytest.raises(LedgerError, match="only 20"):
            ledger.add_fill(s, ledger.make_fill(D(2026, 1, 7), "SELL", "AAA", 21, 55.0))
        ledger.add_fill(s, ledger.make_fill(D(2026, 1, 7), "SELL", "AAA", 20, 55.0))
    with session_scope(engine) as s:
        assert [f.symbol for f in ledger.fills(s)] == ["AAA", "AAA"]
        assert market.closes(s, ["AAA"])["AAA"].tolist() == [100.0, 50.0]


def test_delete_is_soft_audited_and_cannot_orphan_a_sell(engine):
    with session_scope(engine) as s:
        buy = ledger.add_fill(
            s, ledger.make_fill(D(2026, 1, 5), "BUY", "AAA", 10, 10.0)
        )
        ledger.add_fill(s, ledger.make_fill(D(2026, 1, 6), "SELL", "AAA", 10, 11.0))
        buy_id = buy.id
    with session_scope(engine) as s, pytest.raises(LedgerError):
        ledger.delete_fill(s, buy_id)  # the sell would then sell shares never bought
    with session_scope(engine) as s:
        sell = ledger.last_fill(s)
        ledger.delete_fill(s, sell.id)
        ledger.update_fill(s, buy_id, {"price": 9.5})
    with session_scope(engine) as s:
        assert len(ledger.fills(s)) == 1 and ledger.fills(s)[0].price == 9.5
        actions = [a.action for a in s.query(AuditRow).order_by(AuditRow.id)]
        assert actions == ["create", "create", "delete", "update"]


def test_activate_rejects_long_short_and_audits(engine):
    with session_scope(engine) as s:
        with pytest.raises(ValueError, match="short"):
            strat_store.activate(s, strat_store.config(s, "ls_momentum"), 10_000)
        cfg = strat_store.config(s, "trend_200d", {"window": 150})
        assert (
            strat_store.config(s, "trend_200d", {"window": 150}).id == cfg.id
        )  # reused
        act = strat_store.activate(s, cfg, 10_000, on=D(2026, 1, 5))
        assert strat_store.spec_for(cfg).params == {"window": 150}
        assert strat_store.label(cfg) == "trend_200d(window=150)"
        assert act.capital_usd == 10_000
        strat_store.update_sizing(s, capital_usd=12_000)
    with session_scope(engine) as s:
        assert strat_store.active(s).capital_usd == 12_000


def test_suggestions_replace_and_expire(engine):
    o = Order("AAA", "BUY", "BUY", 5, 10.0, 0.5, 0, "enters")
    with session_scope(engine) as s:
        cfg = strat_store.config(s, "trend_200d")
        daily_store.replace_suggestions(s, D(2026, 1, 5), D(2026, 1, 6), cfg.id, [o])
        daily_store.replace_suggestions(
            s, D(2026, 1, 5), D(2026, 1, 6), cfg.id, [o]
        )  # re-run: no duplicate
        assert len(daily_store.suggestions(s, D(2026, 1, 5))) == 1
        daily_store.replace_suggestions(s, D(2026, 1, 6), D(2026, 1, 7), cfg.id, [])
        assert daily_store.suggestions(s, D(2026, 1, 5))[0].status == "expired"


def test_hand_entered_fill_is_matched_to_its_suggestion(engine):
    o = Order("AAA", "BUY", "BUY", 5, 10.0, 0.5, 0, "enters")
    with session_scope(engine) as s:
        cfg = strat_store.config(s, "trend_200d")
        [sug] = daily_store.replace_suggestions(
            s, D(2026, 1, 5), D(2026, 1, 6), cfg.id, [o]
        )
        ledger.add_fill(s, ledger.make_fill(D(2026, 1, 6), "BUY", "AAA", 3, 10.1))
        daily_store.match_unlinked_fills(s, D(2026, 1, 5))
        assert sug.status == "partial" and sug.filled_qty == 3


def test_run_queue_claims_oldest_and_records_failure(engine):
    with session_scope(engine) as s:
        cfg = strat_store.config(s, "trend_200d")
        kw = dict(
            config_id=cfg.id,
            start=D(2005, 1, 1),
            split=D(2018, 1, 1),
            commission_bps=1,
            slippage_bps=4,
            execution="next_open",
        )
        made = runs.submit(s, [BacktestRunRow(**kw), BacktestRunRow(**kw)])
        assert made[0].sweep_id == made[1].sweep_id is not None
        first = runs.claim_next(s)
        assert first.id == made[0].id and first.status == "running"
        runs.finish(s, first, None, "boom")
        assert first.status == "failed" and "boom" in first.error
        assert runs.requeue_stale(s) == 0


def test_cash_kinds_are_validated(engine):
    with session_scope(engine) as s:
        with pytest.raises(LedgerError, match="kind"):
            ledger.add_cash(s, D(2026, 1, 5), "gift", 10)
        with pytest.raises(LedgerError, match="positive"):
            ledger.add_cash(s, D(2026, 1, 5), "deposit", -5)
        ledger.add_cash(s, D(2026, 1, 5), "deposit", 1000)
        _, flows, _ = ledger.records(s)
        assert flows[0].kind == "deposit" and flows[0].amount == 1000
