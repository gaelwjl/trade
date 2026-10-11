"""Database tables (SQLAlchemy 2). Fills and cash flows are the facts you enter; every other
table is written by the daily job or the backtest worker and can be rebuilt."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


# ---------- facts you enter ----------
class FillRow(Base):
    __tablename__ = "fills"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_date: Mapped[dt.date] = mapped_column(Date, index=True)
    side: Mapped[str] = mapped_column(String(4))  # BUY | SELL
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    strategy: Mapped[str] = mapped_column(String(64), default="")
    suggestion_id: Mapped[int | None] = mapped_column(
        ForeignKey("suggestions.id"), nullable=True
    )
    note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class CashFlowRow(Base):
    __tablename__ = "cash_flows"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(
        String(16)
    )  # deposit | withdrawal | fee | interest
    amount: Mapped[float] = mapped_column(
        Float
    )  # always positive; the kind sets the sign
    note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


# ---------- market data for valuation ----------
class RawCloseRow(Base):
    __tablename__ = "raw_closes"
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    close: Mapped[float] = mapped_column(Float)


class CorporateActionRow(Base):
    __tablename__ = "corporate_actions"
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    ex_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), primary_key=True)  # split | dividend
    value: Mapped[float] = mapped_column(Float)  # split ratio, or raw cash per share


# ---------- strategies ----------
class StrategyConfigRow(Base):
    __tablename__ = "strategy_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_name: Mapped[str] = mapped_column(String(64), index=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    code_sha: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class ActiveStrategyRow(Base):
    __tablename__ = "active_strategy"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # always 1
    config_id: Mapped[int] = mapped_column(ForeignKey("strategy_configs.id"))
    promoted_from_run: Mapped[int | None] = mapped_column(
        ForeignKey("backtest_runs.id"), nullable=True
    )
    capital_usd: Mapped[float] = mapped_column(Float)
    min_order_usd: Mapped[float] = mapped_column(Float, default=100.0)
    fractional: Mapped[bool] = mapped_column(Boolean, default=False)
    activated_at: Mapped[dt.date] = mapped_column(Date)


# ---------- daily outputs ----------
class SignalRunRow(Base):
    __tablename__ = "signal_runs"
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    config_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_configs.id"), primary_key=True
    )
    is_rebalance: Mapped[bool] = mapped_column(Boolean)
    next_rebalance: Mapped[dt.date] = mapped_column(Date)


class TargetRow(Base):
    __tablename__ = "targets"
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    config_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_configs.id"), primary_key=True
    )
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    weight: Mapped[float] = mapped_column(Float)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)


class SuggestionRow(Base):
    __tablename__ = "suggestions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_date: Mapped[dt.date] = mapped_column(Date, index=True)
    for_session: Mapped[dt.date] = mapped_column(Date)
    config_id: Mapped[int] = mapped_column(ForeignKey("strategy_configs.id"))
    symbol: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(8))  # BUY | SELL | ADD | TRIM
    side: Mapped[str] = mapped_column(String(4))  # BUY | SELL
    qty: Mapped[float] = mapped_column(Float)
    ref_price: Mapped[float] = mapped_column(Float)
    target_weight: Mapped[float] = mapped_column(Float)
    held_qty: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(300))
    # open | done | partial | dismissed | expired
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class PortfolioSnapshotRow(Base):
    __tablename__ = "portfolio_snapshots"
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    market_value: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)
    net_flows: Mapped[float] = mapped_column(Float)
    deposits_cum: Mapped[float] = mapped_column(Float)
    realized_cum: Mapped[float] = mapped_column(Float)
    unrealized: Mapped[float] = mapped_column(Float)
    dividends_cum: Mapped[float] = mapped_column(Float)
    other_cum: Mapped[float] = mapped_column(Float)
    day_pnl: Mapped[float] = mapped_column(Float)
    twr_index: Mapped[float] = mapped_column(Float)


class PaperSnapshotRow(Base):
    __tablename__ = "paper_snapshots"
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    config_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_configs.id"), primary_key=True
    )
    equity: Mapped[float] = mapped_column(Float)
    positions: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------- backtests and jobs ----------
class BacktestRunRow(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("strategy_configs.id"))
    sweep_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    start: Mapped[dt.date] = mapped_column(Date)
    end: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    split: Mapped[dt.date] = mapped_column(Date)
    commission_bps: Mapped[float] = mapped_column(Float)
    slippage_bps: Mapped[float] = mapped_column(Float)
    execution: Mapped[str] = mapped_column(String(16))
    cost_levels: Mapped[list] = mapped_column(JSON, default=list)
    data_as_of: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    data_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    code_sha: Mapped[str] = mapped_column(String(40), default="")
    # queued | running | done | failed
    status: Mapped[str] = mapped_column(String(10), default="queued", index=True)
    error: Mapped[str] = mapped_column(String(2000), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class BacktestResultRow(Base):
    __tablename__ = "backtest_results"
    run_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_runs.id"), primary_key=True
    )
    stats_by_period: Mapped[dict] = mapped_column(JSON)
    periods: Mapped[list] = mapped_column(JSON)
    annual: Mapped[dict] = mapped_column(JSON)
    cost_sensitivity: Mapped[dict] = mapped_column(JSON)
    equity_path: Mapped[str] = mapped_column(String(300))


class JobRunRow(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(
        String(16), index=True
    )  # daily | backtest | backup
    session_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(10))  # running | done | failed
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    log_tail: Mapped[str] = mapped_column(String(8000), default="")


class AuditRow(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    entity: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(16))  # create | update | delete
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class WatchlistRow(Base):
    __tablename__ = "watchlist"
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    added_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
