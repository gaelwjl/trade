"""Command line: ``python -m tradelab <command>`` (or ``tradelab <command>`` once installed).

    fetch                                  refresh constituents + prices (and any holdings outside the index)
    report     [--start --end --split ...] backtest every strategy; print stats; write reports/suite.png
    screen     [--preset P] [--sort COL]   screener table in the terminal
    signals    [--days N]                  what each strategy trades at the next open (cached prices)
    portfolio  add|list|trades|undo        your trade ledger
    dashboard  screener|backtests|signals|all [--open]
    daily      [--no-fetch] [--all] [--open]   fetch, check every strategy, rebuild the dashboards

Only this module parses arguments and prints; everything it calls returns data.
"""
from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path

import pandas as pd

from .backtest import runner
from .backtest.engine import CostModel
from .dashboard import backtests_page, plots, screener_page, signals_page
from .data import fetch as fetcher
from .data import market_calendar as cal
from .data.prices import NASDAQ100, ROOT, Panel
from .live import portfolio as pf
from .live import screener, signals
from .strategies.registry import REGISTRY
from .types import StatsTable

REPORTS = ROOT / "reports"
PAGES = {"screener": REPORTS / "screener.html", "backtests": REPORTS / "backtests.html", "signals": REPORTS / "signals.html"}
DISCLAIMER = "(mechanical strategy output on a survivorship-biased universe; not investment advice)"


# ---------- formatting ----------
def fmt_stats(df: StatsTable) -> str:
    pct = {"CAGR", "Vol", "MaxDD", "Alpha", "Turnover/yr", "AvgExposure", "CostDrag/yr"}
    out = df.copy().astype(object)
    for c in out.columns:
        out[c] = df[c].map(lambda v: f"{v:.1%}" if c in pct else f"{v:.2f}")
    return out.to_string()


def signals_text(by_strategy: dict[str, list[signals.Decision]], day: str, next_session: str,
                 stale_msg: str | None = None) -> str:
    lines = [f"Signals after the close of {day} -> orders for the open of {next_session}", DISCLAIMER]
    if stale_msg:
        lines.append(stale_msg)
    for name, decisions in by_strategy.items():
        d = next((x for x in decisions if str(x.date) == day), None)
        if d is None:
            continue
        head = f"{name:18s} [{REGISTRY[name].rebalance}] "
        if not d.is_rebalance:
            lines.append(f"{head}no trade; {len(d.holdings)} positions; next rebalance {d.next_rebalance}")
            continue
        by: dict[str, list[str]] = {}
        for t in d.trades:
            by.setdefault(t.action, []).append(t.symbol)
        if not by:
            lines.append(f"{head}checked, no changes; {len(d.holdings)} positions; next check {d.next_rebalance}")
            continue
        lines.append(f"{head}REBALANCE -> " + "; ".join(f"{a} {', '.join(v)}" for a, v in by.items()))
    return "\n".join(lines)


def _open(path: Path) -> None:
    webbrowser.open(path.resolve().as_uri())


# ---------- commands ----------
def cmd_fetch(_: argparse.Namespace) -> None:
    held = list(pf.positions(pf.load_ledger())[0].index)
    rep = fetcher.refresh(extra_symbols=held)
    print(f"Constituents: {rep.constituents} (as of {rep.as_of})")
    print(f"Downloaded {len(rep.requested) - len(rep.missing)}/{len(rep.requested)} symbols")
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


def cmd_report(a: argparse.Namespace) -> None:
    panel = Panel.load()
    costs = CostModel(a.commission_bps, a.slippage_bps)
    res = runner.run_suite(panel, a.start, a.end, costs, "next_close" if a.next_close else "next_open", a.cash_rate)
    print(f"Universe: {len(panel.symbols)} CURRENT Nasdaq-100 members (survivorship-biased) | "
          f"{a.start} -> {panel.close.index[-1].date()} | one-way cost {costs.one_way * 1e4:.0f} bps\n")
    print("FULL PERIOD  (Beta/Alpha/Corr vs QQQ)")
    print(fmt_stats(runner.table(res)), "\n")
    for label, lo, hi in [(f"IN-SAMPLE  ({a.start} .. {a.split})", a.start, a.split),
                          (f"OUT-OF-SAMPLE ({a.split} ..)", a.split, None)]:
        t = runner.table({k: r.between(lo, hi) for k, r in res.items()})
        print(label)
        print(fmt_stats(StatsTable(t[["CAGR", "Vol", "Sharpe", "MaxDD", "Calmar"]])), "\n")
    png = plots.suite_png(res, REPORTS / "suite.png", "Strategy suite vs QQQ (survivor universe, net of costs)")
    print(f"chart -> {png}")


def cmd_screen(a: argparse.Namespace) -> None:
    panel = Panel.load(dedupe=False)
    f = screener.screen(panel)
    if a.preset:
        f = screener.PRESETS[a.preset](f)
    if a.sort:
        f = f.sort_values(a.sort, ascending=False)
    cols = ["name", "price", "chg_1d", "chg_1m", "mom_12_1", "from_high", "rsi", "vs200", "vol_ratio"]
    out = f[cols].head(a.top).copy()
    out["name"] = out["name"].str[:24]
    pct = ["chg_1d", "chg_1m", "mom_12_1", "from_high", "vs200"]
    out[pct] = (out[pct] * 100).round(1)
    print(f"{len(f)} matches (as of {panel.close.index[-1].date()})")
    print(out.round(2).to_string())


def _signals(days: int) -> tuple[dict[str, list[signals.Decision]], str, str, str | None]:
    panels = signals.load_panels(REGISTRY.values())
    by = signals.latest(days, panels)
    last = panels[NASDAQ100].close.index[-1].date()
    expected = signals.expected_last_session()
    stale = (f"WARNING: data ends {last} but {expected} has closed. Run: python -m tradelab fetch"
             if last < expected else None)
    return by, str(last), str(cal.next_session(last)), stale


def cmd_signals(a: argparse.Namespace) -> None:
    print(signals_text(*_signals(a.days)))
    signals_page.build(PAGES["signals"], a.days)
    print(f"\npage -> {PAGES['signals']}")


def cmd_portfolio(a: argparse.Namespace) -> None:
    if a.action == "add":
        t = pf.make_trade(a.side, a.symbol, a.qty, a.price, a.date, a.fees, a.strategy, a.note)
        pf.append_trade(t)
        print(f"recorded: {t['date']} {t['side']} {t['qty']:g} {t['symbol']} @ {t['price']:g}"
              + (f" [{t['strategy']}]" if t["strategy"] else ""))
    elif a.action == "undo":
        removed = pf.undo_last()
        print(f"removed: {removed}" if removed else "ledger is empty")
    elif a.action == "trades":
        led = pf.load_ledger()
        print(led.to_string(index=False) if len(led) else f"no trades recorded yet ({pf.LEDGER})")
        return
    pos, realized = pf.positions(pf.load_ledger())
    if not len(pos):
        print(f"no open positions (ledger: {pf.LEDGER})")
        return
    v = pf.valued(pos)
    show = v[["qty", "avg_cost", "last", "value", "pnl", "pnl_pct", "strategies"]].copy()
    show["pnl_pct"] = (show["pnl_pct"] * 100).round(2)
    print(show.round(2).to_string())
    print(f"\ncost {v['cost'].sum():,.2f} | value {v['value'].sum():,.2f} | unrealized {v['pnl'].sum():+,.2f} "
          f"| realized {realized:+,.2f}")


def build_pages(which: list[str], a: argparse.Namespace) -> list[Path]:
    built = []
    for page in which:
        out = PAGES[page]
        if page == "screener":
            screener_page.build(out, getattr(a, "years", 2))
        elif page == "backtests":
            backtests_page.build(out, getattr(a, "start", "2005-01-01"), getattr(a, "split", "2018-01-01"),
                                 [float(x) for x in getattr(a, "costs", "0,5,10,20").split(",")])
        else:
            signals_page.build(out, getattr(a, "days", 63))
        print(f"{page:9s} -> {out} ({out.stat().st_size / 1e6:.1f} MB)")
        built.append(out)
    return built


def cmd_dashboard(a: argparse.Namespace) -> None:
    pages = list(PAGES) if a.page == "all" else [a.page]
    built = build_pages(pages, a)
    if a.open:
        _open(built[-1])


def cmd_daily(a: argparse.Namespace) -> None:
    if not a.no_fetch:
        cmd_fetch(a)
        print()
    by, day, nxt, stale = _signals(63)
    print(signals_text(by, day, nxt, stale))
    held = set(pf.positions(pf.load_ledger())[0].index)
    exits = sorted({t.symbol for ds in by.values() for d in ds if str(d.date) == day
                    for t in d.trades if t.symbol in held and t.action in ("SELL", "TRIM")})
    print(f"\nYour positions: {len(held)}" + (f" | strategy exit/trim signals on: {', '.join(exits)}" if exits else ""))
    print()
    built = build_pages(["signals", "screener", *(["backtests"] if a.all else [])], a)
    if a.open:
        _open(built[0])


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="tradelab", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("fetch", help="refresh constituents + prices").set_defaults(fn=cmd_fetch)

    r = sub.add_parser("report", help="backtest every strategy")
    r.add_argument("--start", default="2005-01-01")
    r.add_argument("--end", default=None)
    r.add_argument("--split", default="2018-01-01", help="in-sample / out-of-sample boundary")
    r.add_argument("--commission-bps", type=float, default=1.0)
    r.add_argument("--slippage-bps", type=float, default=4.0)
    r.add_argument("--cash-rate", type=float, default=0.0, help="annual rate earned on idle cash")
    r.add_argument("--next-close", action="store_true", help="fill at next close instead of next open")
    r.set_defaults(fn=cmd_report)

    s = sub.add_parser("screen", help="screener table")
    s.add_argument("--preset", choices=list(screener.PRESETS))
    s.add_argument("--sort", default=None, help="column to sort by (descending)")
    s.add_argument("--top", type=int, default=25)
    s.set_defaults(fn=cmd_screen)

    g = sub.add_parser("signals", help="what each strategy trades at the next open")
    g.add_argument("--days", type=int, default=63, help="sessions of history in the page")
    g.set_defaults(fn=cmd_signals)

    p = sub.add_parser("portfolio", help="your trade ledger")
    psub = p.add_subparsers(dest="action", required=True)
    add = psub.add_parser("add", help="record a fill")
    add.add_argument("side", choices=["BUY", "SELL", "buy", "sell"])
    add.add_argument("symbol")
    add.add_argument("qty", type=float)
    add.add_argument("price", type=float)
    add.add_argument("--date", help="YYYY-MM-DD (default: today, US/Eastern)")
    add.add_argument("--fees", type=float, default=0.0)
    add.add_argument("--strategy", default="", help="strategy whose signal this follows: " + ", ".join(pf.STRATEGY_TAGS))
    add.add_argument("--note", default="")
    for name, help_ in [("list", "open positions"), ("trades", "the ledger"), ("undo", "remove the last entry")]:
        psub.add_parser(name, help=help_)
    p.set_defaults(fn=cmd_portfolio)

    d = sub.add_parser("dashboard", help="build HTML dashboards into reports/")
    d.add_argument("page", choices=[*PAGES, "all"])
    d.add_argument("--open", action="store_true")
    d.add_argument("--years", type=int, default=2, help="screener: years of chart history")
    d.add_argument("--start", default="2005-01-01", help="backtests: first date")
    d.add_argument("--split", default="2018-01-01", help="backtests: in/out-of-sample boundary")
    d.add_argument("--costs", default="0,5,10,20", help="backtests: one-way cost levels (bps)")
    d.add_argument("--days", type=int, default=63, help="signals: sessions of history")
    d.set_defaults(fn=cmd_dashboard)

    y = sub.add_parser("daily", help="fetch, check every strategy, rebuild dashboards (run after 16:15 ET)")
    y.add_argument("--no-fetch", action="store_true", help="use cached prices")
    y.add_argument("--all", action="store_true", help="also rebuild the backtests page")
    y.add_argument("--open", action="store_true")
    y.set_defaults(fn=cmd_daily)
    return ap


def main(argv: list[str] | None = None) -> None:
    a = parser().parse_args(argv)
    pd.set_option("display.width", 200)
    a.fn(a)
