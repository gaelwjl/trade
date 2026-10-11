# tradelab

A local, single-user web app for Nasdaq-100 trading research: it remembers your portfolio and values
it after every close, runs strategy backtests as batch jobs, and turns one promoted strategy into a
daily list of suggested orders. Data comes from Yahoo Finance; you place every order yourself.

Design: [docs/2026-10-11-web-app-design/design.md](docs/2026-10-11-web-app-design/design.md).

## Run it
```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # installs the `tradelab` command
tradelab service install               # web app, backtest worker and scheduler, started at login (launchd)
open http://127.0.0.1:8000             # Today, Portfolio, Strategy, Backtests, Signals, Screener, Settings
tradelab service status                # or: uninstall; logs are in data/logs/
```
`python -m tradelab <command>` works without installing. `tradelab --db FILE ...` points any command at
another database (handy for experiments).

## Daily use
1. **Record cash and fills**: Portfolio page, or `tradelab portfolio cash deposit 10000` and
   `tradelab portfolio add BUY NVDA 10 229.50 --strategy trend_200d` (also `list`, `fills`, `undo`).
2. **Pick a strategy**: queue backtests on the Backtests page (or `tradelab backtest submit trend_200d`),
   compare runs, then promote one with a fixed dollar capital. The dialog previews today's target and orders.
3. **Every session after 16:30 ET** the scheduler fetches prices, recomputes every strategy, values your
   portfolio and writes suggested orders for the next open. Today shows them; record a fill from a
   suggestion with one click, or dismiss it. `tradelab daily` runs the same job by hand.

Suggestions are the mechanical output of the strategy you activated, not investment advice.

## Other commands
```bash
tradelab report | screen | signals     # terminal reports; `tradelab dashboard all` writes static HTML to reports/
tradelab backup | restore FILE         # data/backups/ (a backup also runs nightly at 23:00 ET)
tradelab worker --once | scheduler | serve   # what the launchd services run
.venv/bin/python -m pytest && .venv/bin/mypy tradelab --ignore-missing-imports && .venv/bin/black --check tradelab tests
```
State lives in `data/tradelab.sqlite` (git-ignored; migrations run automatically). Python is formatted
with Black 26.1.0 (`[tool.black]` in pyproject.toml), the version the VS Code extension bundles.

## Layout
Dependencies point downward: `cli`, `api` and `web` use the packages below them; `data`, `strategies`,
`backtest` and `live` never import storage or presentation code and never print.

| Package | Modules | Responsibility |
|---|---|---|
| `data` | `prices`, `market_calendar`, `fetch` | constituents, adjusted-price cache, raw closes + corporate actions, `Panel`, NYSE calendar |
| `strategies` | `indicators`, `library`, `registry` | indicators; strategy functions + the score each decides on; `REGISTRY` of `StrategySpec`s |
| `backtest` | `engine`, `metrics`, `runner`, `analysis` | `backtest(...)`, stats, suite/sweeps/benchmarks, period/annual/rolling/cost analytics |
| `live` | `signals`, `screener`, `portfolio`, `suggestions` | per-day `Decision`s, screener, FIFO P&L engine, suggested-order sizing (all pure) |
| `store` | `models`, `db`, `ledger`, `market`, `strategies`, `daily`, `runs`, `views` | SQLite tables + Alembic migrations; audited fills/cash; runs, targets, suggestions |
| `jobs` | `daily`, `worker`, `scheduler`, `backup` | the daily pipeline, the backtest queue, the 16:30 ET schedule, backups |
| `api` | `app`, `portfolio`, `strategy`, `backtests`, `system` | FastAPI JSON API under `/api` (docs at `/docs`) |
| `web` | `pages`, `render`, `*_page`, `plots`, `templates/` | HTML pages (served, or exported as static files) and the PNG chart |
| — | `cli`, `types` | the command line; named frame types used in every signature |

## Portfolio accounting
Fills and cash flows are the only facts you enter; everything else is recomputed from them. Lots are
first-in, first-out; trade fees go into cost basis and proceeds. Valuation uses raw closes (Yahoo's
split adjustment undone), splits rescale lots, and dividends are credited on their ex-date (Yahoo has no
pay dates). Returns are time-weighted, so deposits and withdrawals don't count as gains. Selling more
than you hold is rejected, edits and deletes are audited, and nothing is ever hard-deleted.

## Timing (no lookahead)
Weights in row *t* use data through day *t*'s close and execute at day *t+1*'s open (or close with
`--next-close`). Tests fail if the engine, any registered strategy, or any strategy's displayed score
peeks ahead. Rebalance days are the last NYSE session of each week/month; a still-running period is
never treated as ended.

## Adding a strategy
Write the function (and its score) in `strategies/library.py`, then register it; backtests, signals and
every page pick it up.
```python
def breakout_score(p: Panel, n: int = 20, **_: object) -> Scores:
    return Scores(p.close / p.close.rolling(n).max() - 1)

def breakout(p: Panel, n: int = 20) -> Weights:
    """Long names at a new n-day high."""
    return equal_weight(Mask(breakout_score(p, n) >= 0))

# strategies/registry.py
StrategySpec("breakout_20d", "Breakout", lib.breakout, lib.breakout_score, "vs 20d high", "return", "W")
```
Explore parameters with a grid on the Backtests page, or `runner.sweep(Panel.load(), REGISTRY["breakout_20d"], {"n": [10, 20, 55]})`.

## Known limitations
- **Survivorship bias (large).** The universe is today's Nasdaq-100 members applied to the past. Compare
  strategies to the `EW universe` benchmark (same bias), not just QQQ. Long-short strategies can't be
  promoted to active.
- Yahoo data is unofficial and can contain errors; the daily job refuses to suggest orders from stale data.
- Cash earns 0% in backtests (`--cash-rate`); Sharpe uses rf = 0. One-off market closures and early closes
  are not in the calendar.
