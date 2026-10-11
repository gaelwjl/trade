"""Static PNG of the backtest suite (equity on a log scale + drawdowns)."""

from __future__ import annotations

from pathlib import Path

from ..backtest.engine import Result


def suite_png(results: dict[str, Result], path: Path, title: str = "") -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (a, b) = plt.subplots(
        2, 1, figsize=(12, 9), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
    )
    for k, r in results.items():
        ls = "--" if k in ("QQQ", "EW universe") else "-"
        a.plot(r.equity.index, r.equity, label=k, ls=ls, lw=1.4)
        b.plot(r.equity.index, r.drawdown, lw=0.9, ls=ls)
    a.set_yscale("log")
    a.set_ylabel("Growth of $1 (log)")
    a.legend(ncol=2, fontsize=8)
    a.grid(alpha=0.3)
    a.set_title(title)
    b.set_ylabel("Drawdown")
    b.grid(alpha=0.3)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path
