# tradelab

Nasdaq-100 data from Yahoo Finance, a daily-bar backtester for low/mid-frequency strategies, live
strategy signals with a personal trade ledger, and offline HTML dashboards.

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # installs the `tradelab` command
tradelab daily --open                  # after 16:15 ET: fetch, check every strategy, rebuild dashboards
tradelab fetch                         # constituents (Nasdaq API) + prices + any holdings outside the index -> data/
tradelab report                        # backtest the suite vs QQQ / equal-weight universe -> reports/suite.png
tradelab report --slippage-bps 20 --next-close     # stress costs / execution
tradelab screen --preset oversold      # terminal screener (oversold|overbought|high|golden|volume)
tradelab signals                       # what each strategy trades at the next open
tradelab portfolio add BUY NVDA 10 229.50 --strategy rsi2_reversion   # record a fill; also list|trades|undo
tradelab dashboard all --open          # screener | backtests | signals | all -> reports/*.html
.venv/bin/python -m pytest && .venv/bin/mypy tradelab --ignore-missing-imports
```
(`python -m tradelab <command>` works without installing.)

## Layout
Dependencies point downward only: `cli` and `dashboard` read from the logic packages; nothing in
`data`, `strategies`, `backtest` or `live` imports presentation code, and none of them print.

| Package | Modules | Responsibility |
|---|---|---|
| `data` | `prices`, `market_calendar`, `fetch` | constituents, adjusted-price cache, `Panel` (wide date x symbol OHLCV), NYSE calendar |
| `strategies` | `indicators`, `library`, `registry` | indicators; strategy functions + the score each decides on; `REGISTRY` of `StrategySpec`s |
| `backtest` | `engine`, `metrics`, `runner`, `analysis` | `backtest(...)`, stats, suite/sweeps/benchmarks, period/annual/rolling/cost analytics |
| `live` | `signals`, `screener`, `portfolio` | inference only: per-day `Decision`s, screener snapshot, your trade ledger + P&L |
| `dashboard` | `render`, `*_page`, `plots`, `templates/` | shape logic results into JSON, render offline HTML pages and the PNG chart |
| — | `cli`, `types` | the one command-line entry point; named frame types used in every signature |

## Timing (no lookahead)
Weights in row *t* use data through day *t*'s close and execute at day *t+1*'s open (or close with
`--next-close`). Tests fail if the engine, any registered strategy, or any strategy's displayed score
peeks ahead. Rebalance days are the last NYSE session of each week/month; a still-running period is
never treated as ended.

## Adding a strategy
Write the function (and its score) in `strategies/library.py`, then register it; backtests, signals and
every dashboard pick it up.
```python
def breakout_score(p: Panel, n: int = 20, **_: object) -> Scores:
    return Scores(p.close / p.close.rolling(n).max() - 1)

def breakout(p: Panel, n: int = 20) -> Weights:
    """Long names at a new n-day high."""
    return equal_weight(Mask(breakout_score(p, n) >= 0))

# strategies/registry.py
StrategySpec("breakout_20d", "Breakout", lib.breakout, lib.breakout_score, "vs 20d high", "return", "W")
```
Explore parameters with `runner.sweep(Panel.load(), REGISTRY["breakout_20d"], {"n": [10, 20, 55]})`.

## Your trades
`data/portfolio/trades.csv` (git-ignored) holds your fills. Positions use average cost; selling more
than you hold is rejected. The signals page shows each holding against the strategy you tagged it with
(IN / EXIT) and any strategy's sell or trim on it.

## Known limitations
- **Survivorship bias (large).** The universe is today's Nasdaq-100 members applied to the past. Compare
  strategies to the `EW universe` benchmark (same bias), not just QQQ. Long-short results are especially
  distorted (shorting survivors).
- Yahoo data is unofficial and adjusted; it can contain errors. Backtests default to starting 2005.
- Cash earns 0% by default (`--cash-rate`); Sharpe uses rf = 0. One-off market closures and early closes
  are not in the calendar.
- Signals are the mechanical output of backtested rules, not investment advice.
