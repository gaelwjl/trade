"""The daily pipeline: fetch, validate, signals, suggestions, valuation (design: Daily pipeline).

Idempotent per session: re-running a date replaces that date's outputs and never touches fills.
It stops at the first failed check and leaves the previous session's numbers in place.
"""

from __future__ import annotations

import datetime as dt
import traceback
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import Engine

from ..backtest.engine import CostModel, backtest
from ..data import fetch as fetcher
from ..data import market_calendar as cal
from ..data import prices
from ..data.prices import NASDAQ100, Panel
from ..live import portfolio as pf
from ..live import signals
from ..live.suggestions import SizingRules, for_strategy
from ..strategies.registry import REGISTRY
from ..store import daily as daily_store
from ..store import ledger, market, runs
from ..store import strategies as strat_store
from ..store.db import session_scope, must_get


class StaleData(RuntimeError):
    """Prices do not reach the last completed session."""


@dataclass
class DailyReport:
    session: dt.date | None = None
    ok: bool = False
    suggestions: int = 0
    warnings: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    def note(self, line: str) -> None:
        self.log.append(line)


def refresh_market(engine: Engine, report: DailyReport) -> None:
    """Adjusted research prices for the universe + raw closes/actions for everything you traded."""
    with session_scope(engine) as s:
        traded = ledger.traded_symbols(s)
    rep = fetcher.refresh(extra_symbols=traded)
    report.note(
        f"fetched {len(rep.requested) - len(rep.missing)}/{len(rep.requested)} symbols"
    )
    if rep.missing:
        report.warnings.append(f"missing from Yahoo: {', '.join(rep.missing)}")
    raw = prices.download_raw(traded)
    with session_scope(engine) as s:
        for sym, hist in raw.items():
            market.save_raw(s, sym, hist)
    report.note(f"raw closes + corporate actions for {len(raw)} traded symbols")
    lost = sorted(set(traded) - set(raw))
    if lost:
        report.warnings.append(f"no raw prices for {', '.join(lost)}")


def validate(panel: Panel, report: DailyReport, now: pd.Timestamp | None) -> dt.date:
    last = panel.close.index[-1].date()
    expected = signals.expected_last_session(now)
    if last < expected:
        raise StaleData(f"prices end {last}, but {expected} has closed")
    moves = panel.returns.iloc[-1].abs()
    big = moves[moves > 0.5]
    if len(big):
        report.warnings.append(
            "single-day moves over 50%: "
            + ", ".join(f"{s} {v:+.0%}" for s, v in big.items())
        )
    return last


def run_signals(engine: Engine, panels: dict[str, Panel], report: DailyReport) -> None:
    """Store every registered strategy's target (default params) plus the active config's."""
    with session_scope(engine) as s:
        configs = [strat_store.config(s, name) for name in REGISTRY]
        act = strat_store.active(s)
        if act is not None and act.config_id not in {c.id for c in configs}:
            configs.append(must_get(s, strat_store.StrategyConfigRow, act.config_id))
        for cfg in configs:
            spec = strat_store.spec_for(cfg)
            d = signals.decisions(spec, panels[spec.universe], 1)[-1]
            daily_store.save_targets(
                s,
                d.date,
                cfg.id,
                d.is_rebalance,
                d.next_rebalance,
                {h.symbol: h.weight for h in d.holdings},
                {h.symbol: h.signal for h in d.holdings},
            )
    report.note(f"targets stored for {len(configs)} strategy configs")


def run_suggestions(
    engine: Engine, panels: dict[str, Panel], report: DailyReport
) -> None:
    with session_scope(engine) as s:
        act = strat_store.active(s)
        if act is None:
            report.note("no active strategy: no suggestions")
            return
        cfg = must_get(s, strat_store.StrategyConfigRow, act.config_id)
        spec = strat_store.spec_for(cfg)
        panel = panels[spec.universe]
        d = signals.decisions(spec, panel, 1)[-1]
        fills, flows, actions = ledger.records(s)
        book = pf.replay(fills, flows, actions)
        orders = for_strategy(
            spec,
            panel,
            d,
            pf.strategy_holdings(book, spec.name),
            SizingRules(act.capital_usd, act.min_order_usd, act.fractional),
            cash_available=book.cash,
        )
        daily_store.replace_suggestions(
            s, d.date, cal.next_session(d.date), cfg.id, orders
        )
        daily_store.match_unlinked_fills(s, d.date)
        report.suggestions = len(orders)
        report.note(
            f"{len(orders)} suggested orders for {cal.next_session(d.date)}"
            + (
                ""
                if d.is_rebalance
                else " (not a rebalance day: membership changes only)"
            )
        )


def run_valuation(engine: Engine, panel: Panel, report: DailyReport) -> None:
    with session_scope(engine) as s:
        fills, flows, actions = ledger.records(s)
        if not fills and not flows:
            report.note("ledger is empty: no snapshots")
            return
        closes = market.closes(s, ledger.traded_symbols(s))
        table, _ = pf.snapshots(
            fills, flows, actions, closes, pd.DatetimeIndex(panel.close.index)
        )
        gap = pf.identity_gap(table).abs().max() if len(table) else 0.0
        if gap > 0.01:
            report.warnings.append(f"P&L identity off by {gap:.2f}")
        daily_store.save_portfolio_snapshots(s, table)
        report.note(f"{len(table)} portfolio snapshots")


def run_paper(engine: Engine, panels: dict[str, Panel], report: DailyReport) -> None:
    """Follow the active strategy exactly from its activation date, at backtest assumptions."""
    with session_scope(engine) as s:
        act = strat_store.active(s)
        if act is None:
            return
        cfg = must_get(s, strat_store.StrategyConfigRow, act.config_id)
        spec = strat_store.spec_for(cfg)
        panel = panels[spec.universe]
        try:
            res = backtest(
                panel,
                spec.weights(panel),
                rebalance=spec.rebalance,
                start=str(act.activated_at),
                costs=CostModel(),
            )
        except ValueError:  # activated too recently: no fill has happened yet
            report.note("paper portfolio: no fills yet")
            return
        equity = res.equity * act.capital_usd
        last = res.weights.iloc[-1]
        daily_store.save_paper(
            s,
            cfg.id,
            equity,
            {str(k): float(v) for k, v in last[last.abs() > 1e-9].items()},
        )
        report.note(f"paper portfolio: {len(equity)} sessions")


def run_daily(
    engine: Engine, fetch: bool = True, now: pd.Timestamp | None = None
) -> DailyReport:
    report = DailyReport()
    with session_scope(engine) as s:
        job_id = runs.start_job(s, "daily", params={"fetch": fetch}).id
    try:
        if fetch:
            refresh_market(engine, report)
        panels = signals.load_panels(REGISTRY.values())
        report.session = validate(panels[NASDAQ100], report, now)
        report.note(f"session {report.session}")
        run_signals(engine, panels, report)
        run_suggestions(engine, panels, report)
        run_valuation(engine, panels[NASDAQ100], report)
        run_paper(engine, panels, report)
        report.ok = True
    except Exception as e:  # recorded in the job log; previous outputs stay in place
        report.note(f"FAILED: {type(e).__name__}: {e}")
        report.note(traceback.format_exc(limit=5))
    with session_scope(engine) as s:
        job = must_get(s, runs.JobRunRow, job_id)
        job.session_date = report.session
        lines = report.log + [f"WARNING: {w}" for w in report.warnings]
        runs.finish_job(s, job, report.ok, "\n".join(lines))
    return report
