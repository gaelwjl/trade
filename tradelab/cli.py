"""Command line: ``tradelab <command>`` (or ``python -m tradelab <command>``).

    serve      [--host --port]              the web app (API + pages)
    worker                                  run queued backtests
    scheduler                               daily job at 16:30 ET on sessions + nightly backup
    service    install|uninstall|status     keep serve/worker/scheduler running via launchd (macOS)
    daily      [--no-fetch]                 run the daily job now
    fetch                                   refresh constituents + prices
    portfolio  add|cash|list|fills|undo   your ledger (stored in data/tradelab.sqlite)
    backtest   submit|list                  queue backtests / show runs
    report     [--start --end --split ...]  backtest every strategy now; print stats; write reports/suite.png
    screen     [--preset P] [--sort COL]    screener table in the terminal
    signals    [--days N]                   what each strategy trades at the next open
    dashboard  screener|backtests|signals|all [--open]   static HTML exports into reports/
    backup | restore FILE                   database backups in data/backups/

Only this module parses arguments and prints; everything it calls returns data.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import plistlib
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

import pandas as pd

from .backtest import runner
from .backtest.engine import CostModel
from .data import fetch as fetcher
from .data import market_calendar as cal
from .data.prices import DATA_DIR, NASDAQ100, ROOT, Panel, last_cached_date
from .live import screener, signals
from .store import daily as daily_store
from .store import ledger, runs, views
from .store import strategies as strat_store
from .store.db import DB_PATH, open_db, session_scope, must_get
from .store.models import BacktestRunRow
from .strategies.registry import REGISTRY
from .types import StatsTable
from .web import backtests_page, plots, screener_page, signals_page

REPORTS = ROOT / "reports"
PAGES = {
    "screener": REPORTS / "screener.html",
    "backtests": REPORTS / "backtests.html",
    "signals": REPORTS / "signals.html",
}
DISCLAIMER = "(mechanical strategy output on a survivorship-biased universe; not investment advice)"
SERVICES = {
    "serve": ["serve"],
    "worker": ["worker"],
    "scheduler": ["scheduler"],
}
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"


# ---------- formatting ----------
def fmt_stats(df: StatsTable) -> str:
    pct = {"CAGR", "Vol", "MaxDD", "Alpha", "Turnover/yr", "AvgExposure", "CostDrag/yr"}
    out = df.copy().astype(object)
    for c in out.columns:
        out[c] = df[c].map(lambda v: f"{v:.1%}" if c in pct else f"{v:.2f}")
    return out.to_string()


def signals_text(
    by_strategy: dict[str, list[signals.Decision]],
    day: str,
    next_session: str,
    stale_msg: str | None = None,
) -> str:
    lines = [
        f"Signals after the close of {day} -> orders for the open of {next_session}",
        DISCLAIMER,
    ]
    if stale_msg:
        lines.append(stale_msg)
    for name, decisions in by_strategy.items():
        d = next((x for x in decisions if str(x.date) == day), None)
        if d is None:
            continue
        head = f"{name:18s} [{REGISTRY[name].rebalance}] "
        if not d.is_rebalance:
            lines.append(
                f"{head}no trade; {len(d.holdings)} positions; next rebalance {d.next_rebalance}"
            )
            continue
        by: dict[str, list[str]] = {}
        for t in d.trades:
            by.setdefault(t.action, []).append(t.symbol)
        if not by:
            lines.append(
                f"{head}checked, no changes; {len(d.holdings)} positions; next check {d.next_rebalance}"
            )
            continue
        lines.append(
            f"{head}REBALANCE -> "
            + "; ".join(f"{a} {', '.join(v)}" for a, v in by.items())
        )
    return "\n".join(lines)


def _open(path: Path) -> None:
    webbrowser.open(path.resolve().as_uri())


_DB: str | None = None  # set by --db


def _engine():  # type: ignore[no-untyped-def]
    return open_db(_DB)


# ---------- app processes ----------
def cmd_serve(a: argparse.Namespace) -> None:
    import uvicorn

    from .api.app import create_app

    print(f"tradelab on http://{a.host}:{a.port}  (API docs at /docs)")
    uvicorn.run(create_app(_engine()), host=a.host, port=a.port, log_level="info")


def cmd_worker(a: argparse.Namespace) -> None:
    from .jobs import worker

    engine = _engine()
    if a.once:
        done = worker.work_once(engine)
        print(f"ran backtest {done}" if done else "queue is empty")
        return
    print("backtest worker running (Ctrl-C to stop)")
    worker.work_forever(engine)


def cmd_scheduler(_: argparse.Namespace) -> None:
    from .jobs import scheduler

    scheduler.serve(_engine())


def _plist(name: str) -> Path:
    return LAUNCH_AGENTS / f"com.tradelab.{name}.plist"


def cmd_service(a: argparse.Namespace) -> None:
    """launchd agents for serve, worker and scheduler (start at login, restart on crash)."""
    venv_exe = Path(sys.executable).with_name(
        "tradelab"
    )  # this project's virtualenv first
    exe = (
        str(venv_exe) if venv_exe.exists() else (shutil.which("tradelab") or "tradelab")
    )
    logs = DATA_DIR / "logs"
    uid = os.getuid()
    for name, args in SERVICES.items():
        path = _plist(name)
        label = f"com.tradelab.{name}"
        if a.action == "install":
            logs.mkdir(parents=True, exist_ok=True)
            LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
            spec = {
                "Label": label,
                "ProgramArguments": [exe, *args],
                "WorkingDirectory": str(ROOT),
                "RunAtLoad": True,
                "KeepAlive": True,
                "StandardOutPath": str(logs / f"{name}.log"),
                "StandardErrorPath": str(logs / f"{name}.log"),
            }
            path.write_bytes(plistlib.dumps(spec))
            subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}", str(path)], capture_output=True
            )
            subprocess.run(
                ["launchctl", "bootstrap", f"gui/{uid}", str(path)], check=True
            )
            print(f"installed and started {label} -> {path}")
        elif a.action == "uninstall":
            subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}", str(path)], capture_output=True
            )
            path.unlink(missing_ok=True)
            print(f"stopped and removed {label}")
        else:
            out = subprocess.run(
                ["launchctl", "print", f"gui/{uid}/{label}"],
                capture_output=True,
                text=True,
            )
            state = next(
                (
                    l.strip()
                    for l in out.stdout.splitlines()
                    if l.strip().startswith("state =")
                ),
                "not installed",
            )
            print(f"{label:28s} {state}")


# ---------- daily ----------
def cmd_daily(a: argparse.Namespace) -> None:
    from .jobs import daily

    engine = _engine()
    rep = daily.run_daily(engine, fetch=not a.no_fetch)
    print("\n".join(rep.log))
    for w in rep.warnings:
        print(f"WARNING: {w}")
    print(f"\ndaily job {'ok' if rep.ok else 'FAILED'} for session {rep.session}")
    with session_scope(engine) as s:
        sig = daily_store.latest_signal_date(s)
        rows = daily_store.suggestions(s, sig) if sig else []
        if rows:
            print(
                f"\nSuggested orders for the open of {rows[0].for_session} {DISCLAIMER}:"
            )
            for r in rows:
                print(
                    f"  {r.action:5s} {r.qty:>8g} {r.symbol:6s} ~${r.ref_price:,.2f}  {r.reason}"
                )
        elif strat_store.active(s) is None:
            print("\nNo active strategy: promote a backtest run to get suggestions.")


def cmd_fetch(_: argparse.Namespace) -> None:
    with session_scope(_engine()) as s:
        held = ledger.traded_symbols(s)
    rep = fetcher.refresh(extra_symbols=held)
    print(f"Constituents: {rep.constituents} (as of {rep.as_of})")
    print(
        f"Downloaded {len(rep.requested) - len(rep.missing)}/{len(rep.requested)} symbols"
    )
    if rep.missing:
        print(f"MISSING: {rep.missing}")
    q = rep.quality
    print("\nData quality (earliest histories, then shortest):")
    print(q.head(5).to_string())
    print(q.sort_values("rows").head(12).to_string())
    if q["gaps"].sum():
        print("\nInterior gaps (Panel.load forward-fills runs of <= 5 bars):")
        print(q[q["gaps"] > 0]["gaps"].to_string())
    flagged = q[q["max_abs_ret"] > 0.5]
    if len(flagged):
        print("\nSuspicious single-day moves >50% (check for bad splits):")
        print(flagged.to_string())


# ---------- portfolio ----------
def _show_positions(s) -> None:  # type: ignore[no-untyped-def]
    view = views.portfolio(s)
    if not len(view.positions):
        print("no open positions")
    else:
        show = view.positions[
            ["qty", "avg_cost", "last", "value", "pnl", "pnl_pct"]
        ].copy()
        show["strategies"] = view.positions["strategies"]
        show["pnl_pct"] = (show["pnl_pct"] * 100).round(2)
        print(show.round(2).to_string())
    if len(view.snapshots):
        last = view.snapshots.iloc[-1]
        print(
            f"\nas of {view.as_of}: equity {last.equity:,.2f} | cash {last.cash:,.2f} | "
            f"total P&L {last.equity - last.deposits_cum:+,.2f} | return {last.twr_index - 1:+.2%}"
        )


def cmd_portfolio(a: argparse.Namespace) -> None:
    with session_scope(_engine()) as s:
        if a.action == "add":
            row = ledger.add_fill(
                s,
                ledger.make_fill(
                    dt.date.fromisoformat(a.date) if a.date else dt.date.today(),
                    a.side,
                    a.symbol,
                    a.qty,
                    a.price,
                    a.fees,
                    a.strategy,
                    a.note,
                ),
            )
            print(
                f"recorded fill #{row.id}: {row.trade_date} {row.side} {row.qty:g} {row.symbol} @ {row.price:g}"
                + (f" [{row.strategy}]" if row.strategy else "")
            )
        elif a.action == "cash":
            flow = ledger.add_cash(
                s,
                dt.date.fromisoformat(a.date) if a.date else dt.date.today(),
                a.kind,
                a.amount,
                a.note,
            )
            print(f"recorded {flow.kind} of {flow.amount:,.2f} on {flow.date}")
        elif a.action == "undo":
            last = ledger.last_fill(s)
            if last is None:
                print("no fills to undo")
                return
            ledger.delete_fill(s, last.id)
            print(
                f"deleted fill #{last.id} ({last.side} {last.qty:g} {last.symbol}); kept in the audit log"
            )
        elif a.action == "fills":
            rows = ledger.fills(s)
            for r in rows:
                print(
                    f"#{r.id:<4} {r.trade_date} {r.side:4s} {r.qty:>8g} {r.symbol:6s} @ {r.price:<10g} fees {r.fees:g} {r.strategy}"
                )
            if not rows:
                print(f"no fills recorded yet ({DB_PATH})")
            return
    with session_scope(_engine()) as s:
        _show_positions(s)


# ---------- backtests ----------
def cmd_backtest(a: argparse.Namespace) -> None:
    with session_scope(_engine()) as s:
        if a.action == "submit":
            runs.check_dates(
                dt.date.fromisoformat(a.start),
                dt.date.fromisoformat(a.split),
                None,
                last_cached_date(),
            )
            params = json.loads(a.params) if a.params else {}
            grid = json.loads(a.grid) if a.grid else {}
            import itertools

            combos = list(itertools.product(*grid.values())) if grid else [()]
            rows = []
            for combo in combos:
                cfg = strat_store.config(
                    s, a.strategy, {**params, **dict(zip(grid, combo))}
                )
                rows.append(
                    BacktestRunRow(
                        config_id=cfg.id,
                        start=dt.date.fromisoformat(a.start),
                        split=dt.date.fromisoformat(a.split),
                        commission_bps=a.commission_bps,
                        slippage_bps=a.slippage_bps,
                        execution=a.execution,
                        cost_levels=[0.0, 5.0, 10.0, 20.0],
                        code_sha=strat_store.code_version(),
                    )
                )
            made = runs.submit(s, rows)
            print(f"queued runs {[r.id for r in made]}; `tradelab worker` runs them")
            return
        for r in runs.runs(s, 50):
            res = runs.result(s, r.id)
            full = res.stats_by_period.get("full", {}) if res else {}
            oos = res.stats_by_period.get("oos", {}) if res else {}
            cfg = must_get(s, strat_store.StrategyConfigRow, r.config_id)
            cagr = f"{full['CAGR']:+.1%}" if full.get("CAGR") is not None else "-"
            sharpe = f"{full['Sharpe']:.2f}" if full.get("Sharpe") is not None else "-"
            osh = f"{oos['Sharpe']:.2f}" if oos.get("Sharpe") is not None else "-"
            print(
                f"#{r.id:<4} {r.status:8s} {strat_store.label(cfg):40s} CAGR {cagr:>7s} Sharpe {sharpe:>5s} OOS {osh:>5s}"
            )


def cmd_report(a: argparse.Namespace) -> None:
    panel = Panel.load()
    costs = CostModel(a.commission_bps, a.slippage_bps)
    res = runner.run_suite(
        panel,
        a.start,
        a.end,
        costs,
        "next_close" if a.next_close else "next_open",
        a.cash_rate,
    )
    print(
        f"Universe: {len(panel.symbols)} CURRENT Nasdaq-100 members (survivorship-biased) | "
        f"{a.start} -> {panel.close.index[-1].date()} | one-way cost {costs.one_way * 1e4:.0f} bps\n"
    )
    print("FULL PERIOD  (Beta/Alpha/Corr vs QQQ)")
    print(fmt_stats(runner.table(res)), "\n")
    for label, lo, hi in [
        (f"IN-SAMPLE  ({a.start} .. {a.split})", a.start, a.split),
        (f"OUT-OF-SAMPLE ({a.split} ..)", a.split, None),
    ]:
        t = runner.table({k: r.between(lo, hi) for k, r in res.items()})
        print(label)
        print(
            fmt_stats(StatsTable(t[["CAGR", "Vol", "Sharpe", "MaxDD", "Calmar"]])), "\n"
        )
    png = plots.suite_png(
        res,
        REPORTS / "suite.png",
        "Strategy suite vs QQQ (survivor universe, net of costs)",
    )
    print(f"chart -> {png}")


def cmd_screen(a: argparse.Namespace) -> None:
    panel = Panel.load(dedupe=False)
    f = screener.screen(panel)
    if a.preset:
        f = screener.PRESETS[a.preset](f)
    if a.sort:
        f = f.sort_values(a.sort, ascending=False)
    cols = [
        "name",
        "price",
        "chg_1d",
        "chg_1m",
        "mom_12_1",
        "from_high",
        "rsi",
        "vs200",
        "vol_ratio",
    ]
    out = f[cols].head(a.top).copy()
    out["name"] = out["name"].str[:24]
    pct = ["chg_1d", "chg_1m", "mom_12_1", "from_high", "vs200"]
    out[pct] = (out[pct] * 100).round(1)
    print(f"{len(f)} matches (as of {panel.close.index[-1].date()})")
    print(out.round(2).to_string())


def _signals(
    days: int,
) -> tuple[dict[str, list[signals.Decision]], str, str, str | None]:
    panels = signals.load_panels(REGISTRY.values())
    by = signals.latest(days, panels)
    last = panels[NASDAQ100].close.index[-1].date()
    expected = signals.expected_last_session()
    stale = (
        f"WARNING: data ends {last} but {expected} has closed. Run: tradelab daily"
        if last < expected
        else None
    )
    return by, str(last), str(cal.next_session(last)), stale


def cmd_signals(a: argparse.Namespace) -> None:
    print(signals_text(*_signals(a.days)))
    with session_scope(_engine()) as s:
        signals_page.build(PAGES["signals"], s, a.days)
    print(f"\npage -> {PAGES['signals']}")


def build_pages(which: list[str], a: argparse.Namespace) -> list[Path]:
    built = []
    for page in which:
        out = PAGES[page]
        if page == "screener":
            screener_page.build(out, getattr(a, "years", 2))
        elif page == "backtests":
            backtests_page.build(
                out,
                getattr(a, "start", "2005-01-01"),
                getattr(a, "split", "2018-01-01"),
                [float(x) for x in getattr(a, "costs", "0,5,10,20").split(",")],
            )
        else:
            with session_scope(_engine()) as s:
                signals_page.build(out, s, getattr(a, "days", 63))
        print(f"{page:9s} -> {out} ({out.stat().st_size / 1e6:.1f} MB)")
        built.append(out)
    return built


def cmd_dashboard(a: argparse.Namespace) -> None:
    pages = list(PAGES) if a.page == "all" else [a.page]
    built = build_pages(pages, a)
    if a.open:
        _open(built[-1])


def cmd_backup(a: argparse.Namespace) -> None:
    from .jobs import backup

    if a.cmd == "restore":
        backup.restore(Path(a.file))
        print(
            f"restored {DB_PATH} from {a.file} (the previous database was kept beside it)"
        )
        return
    print(f"backup -> {backup.backup()}")


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="tradelab", description=__doc__.split("\n")[0])
    ap.add_argument(
        "--db",
        default=None,
        help=f"database file (default: {DB_PATH.relative_to(ROOT)})",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("serve", help="run the web app")
    v.add_argument(
        "--host", default="127.0.0.1", help="127.0.0.1 = reachable from this Mac only"
    )
    v.add_argument("--port", type=int, default=8000)
    v.set_defaults(fn=cmd_serve)

    w = sub.add_parser("worker", help="run queued backtests")
    w.add_argument(
        "--once", action="store_true", help="run one queued backtest and exit"
    )
    w.set_defaults(fn=cmd_worker)

    sub.add_parser("scheduler", help="daily job + nightly backup").set_defaults(
        fn=cmd_scheduler
    )

    sv = sub.add_parser(
        "service", help="launchd agents for serve, worker and scheduler"
    )
    sv.add_argument("action", choices=["install", "uninstall", "status"])
    sv.set_defaults(fn=cmd_service)

    y = sub.add_parser(
        "daily", help="run the daily job now (after 16:15 ET for final prices)"
    )
    y.add_argument("--no-fetch", action="store_true", help="use cached prices")
    y.set_defaults(fn=cmd_daily)

    sub.add_parser("fetch", help="refresh constituents + prices").set_defaults(
        fn=cmd_fetch
    )

    p = sub.add_parser("portfolio", help="your ledger")
    psub = p.add_subparsers(dest="action", required=True)
    add = psub.add_parser("add", help="record a fill")
    add.add_argument("side", choices=["BUY", "SELL", "buy", "sell"])
    add.add_argument("symbol")
    add.add_argument("qty", type=float)
    add.add_argument("price", type=float)
    add.add_argument("--date", help="YYYY-MM-DD (default: today)")
    add.add_argument("--fees", type=float, default=0.0)
    add.add_argument(
        "--strategy",
        default="",
        help="strategy whose signal this follows: " + ", ".join([*REGISTRY, "manual"]),
    )
    add.add_argument("--note", default="")
    cash = psub.add_parser("cash", help="record a deposit, withdrawal, fee or interest")
    cash.add_argument("kind", choices=["deposit", "withdrawal", "fee", "interest"])
    cash.add_argument("amount", type=float)
    cash.add_argument("--date", help="YYYY-MM-DD (default: today)")
    cash.add_argument("--note", default="")
    for name, help_ in [
        ("list", "positions and P&L"),
        ("fills", "the fill ledger"),
        ("undo", "delete the last fill"),
    ]:
        psub.add_parser(name, help=help_)
    p.set_defaults(fn=cmd_portfolio)

    b = sub.add_parser("backtest", help="queue backtests or list runs")
    bsub = b.add_subparsers(dest="action", required=True)
    bs = bsub.add_parser("submit", help="queue a run (or a grid)")
    bs.add_argument("strategy", choices=list(REGISTRY))
    bs.add_argument("--params", help='JSON, e.g. {"lookback": 126}')
    bs.add_argument("--grid", help='JSON, e.g. {"window": [100, 200]}')
    bs.add_argument("--start", default="2005-01-01")
    bs.add_argument("--split", default="2018-01-01")
    bs.add_argument("--commission-bps", type=float, default=1.0)
    bs.add_argument("--slippage-bps", type=float, default=4.0)
    bs.add_argument(
        "--execution", choices=["next_open", "next_close"], default="next_open"
    )
    bsub.add_parser("list", help="recent runs")
    b.set_defaults(fn=cmd_backtest)

    r = sub.add_parser("report", help="backtest every strategy now")
    r.add_argument("--start", default="2005-01-01")
    r.add_argument("--end", default=None)
    r.add_argument(
        "--split", default="2018-01-01", help="in-sample / out-of-sample boundary"
    )
    r.add_argument("--commission-bps", type=float, default=1.0)
    r.add_argument("--slippage-bps", type=float, default=4.0)
    r.add_argument(
        "--cash-rate", type=float, default=0.0, help="annual rate earned on idle cash"
    )
    r.add_argument(
        "--next-close",
        action="store_true",
        help="fill at next close instead of next open",
    )
    r.set_defaults(fn=cmd_report)

    s = sub.add_parser("screen", help="screener table")
    s.add_argument("--preset", choices=list(screener.PRESETS))
    s.add_argument("--sort", default=None, help="column to sort by (descending)")
    s.add_argument("--top", type=int, default=25)
    s.set_defaults(fn=cmd_screen)

    g = sub.add_parser("signals", help="what each strategy trades at the next open")
    g.add_argument(
        "--days", type=int, default=63, help="sessions of history in the page"
    )
    g.set_defaults(fn=cmd_signals)

    d = sub.add_parser("dashboard", help="static HTML exports into reports/")
    d.add_argument("page", choices=[*PAGES, "all"])
    d.add_argument("--open", action="store_true")
    d.add_argument(
        "--years", type=int, default=2, help="screener: years of chart history"
    )
    d.add_argument("--start", default="2005-01-01", help="backtests: first date")
    d.add_argument(
        "--split", default="2018-01-01", help="backtests: in/out-of-sample boundary"
    )
    d.add_argument(
        "--costs", default="0,5,10,20", help="backtests: one-way cost levels (bps)"
    )
    d.add_argument("--days", type=int, default=63, help="signals: sessions of history")
    d.set_defaults(fn=cmd_dashboard)

    sub.add_parser("backup", help="back up the database now").set_defaults(
        fn=cmd_backup
    )
    rs = sub.add_parser("restore", help="restore the database from a backup file")
    rs.add_argument("file")
    rs.set_defaults(fn=cmd_backup)
    return ap


def main(argv: list[str] | None = None) -> None:
    global _DB
    a = parser().parse_args(argv)
    _DB = a.db
    pd.set_option("display.width", 200)
    try:
        a.fn(a)
    except (
        ValueError,
        KeyError,
    ) as e:  # ledger and lookup errors: one line, not a traceback
        sys.exit(f"error: {e.args[0] if e.args else e}")
