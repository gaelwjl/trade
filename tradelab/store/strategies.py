"""Pinned strategy configs and the one active strategy."""

from __future__ import annotations

import datetime as dt
import subprocess

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..data.prices import ROOT
from ..strategies.registry import REGISTRY, StrategySpec
from .models import ActiveStrategyRow, AuditRow, StrategyConfigRow


def code_version() -> str:
    """Short git commit of the working tree, suffixed ``-dirty`` when it has local changes."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def config(
    s: Session, strategy_name: str, params: dict | None = None
) -> StrategyConfigRow:
    """The config row for ``strategy_name`` + ``params``, created on first use."""
    if strategy_name not in REGISTRY:
        raise KeyError(f"unknown strategy {strategy_name!r}")
    params = params or {}
    q = select(StrategyConfigRow).where(
        StrategyConfigRow.strategy_name == strategy_name
    )
    for row in s.scalars(q):
        if row.params == params:
            return row
    row = StrategyConfigRow(
        strategy_name=strategy_name, params=params, code_sha=code_version()
    )
    s.add(row)
    s.flush()
    return row


def find_config(
    s: Session, strategy_name: str, params: dict | None = None
) -> StrategyConfigRow | None:
    """The config row if it exists (never creates one)."""
    q = select(StrategyConfigRow).where(
        StrategyConfigRow.strategy_name == strategy_name
    )
    return next((r for r in s.scalars(q) if r.params == (params or {})), None)


def spec_for(row: StrategyConfigRow) -> StrategySpec:
    import dataclasses

    base = REGISTRY[row.strategy_name]
    return dataclasses.replace(base, params={**base.params, **row.params})


def label(row: StrategyConfigRow) -> str:
    if not row.params:
        return row.strategy_name
    args = ", ".join(f"{k}={v}" for k, v in sorted(row.params.items()))
    return f"{row.strategy_name}({args})"


def active(s: Session) -> ActiveStrategyRow | None:
    return s.get(ActiveStrategyRow, 1)


def activate(
    s: Session,
    config_row: StrategyConfigRow,
    capital_usd: float,
    min_order_usd: float = 100.0,
    fractional: bool = False,
    run_id: int | None = None,
    on: dt.date | None = None,
) -> ActiveStrategyRow:
    spec = spec_for(config_row)
    if not spec.long_only:
        raise ValueError(
            f"{spec.name} holds short positions; only long-only strategies can be active"
        )
    if capital_usd <= 0:
        raise ValueError("capital must be positive")
    row = active(s)
    before = None if row is None else _active_dict(row)
    if row is None:
        row = ActiveStrategyRow(
            id=1,
            config_id=config_row.id,
            capital_usd=capital_usd,
            activated_at=on or dt.date.today(),
        )
        s.add(row)
    row.config_id = config_row.id
    row.capital_usd = capital_usd
    row.min_order_usd = min_order_usd
    row.fractional = fractional
    row.promoted_from_run = run_id
    row.activated_at = on or dt.date.today()
    s.flush()
    s.add(
        AuditRow(
            entity="active_strategy",
            entity_id=1,
            action="update",
            before=before,
            after=_active_dict(row),
        )
    )
    return row


def update_sizing(
    s: Session,
    capital_usd: float | None = None,
    min_order_usd: float | None = None,
    fractional: bool | None = None,
) -> ActiveStrategyRow:
    row = active(s)
    if row is None:
        raise KeyError("no active strategy")
    before = _active_dict(row)
    if capital_usd is not None:
        if capital_usd <= 0:
            raise ValueError("capital must be positive")
        row.capital_usd = capital_usd
    if min_order_usd is not None:
        row.min_order_usd = max(0.0, min_order_usd)
    if fractional is not None:
        row.fractional = fractional
    s.add(
        AuditRow(
            entity="active_strategy",
            entity_id=1,
            action="update",
            before=before,
            after=_active_dict(row),
        )
    )
    return row


def deactivate(s: Session) -> None:
    row = active(s)
    if row is not None:
        s.add(
            AuditRow(
                entity="active_strategy",
                entity_id=1,
                action="delete",
                before=_active_dict(row),
                after=None,
            )
        )
        s.delete(row)


def _active_dict(row: ActiveStrategyRow) -> dict:
    return {
        "config_id": row.config_id,
        "capital_usd": row.capital_usd,
        "min_order_usd": row.min_order_usd,
        "fractional": row.fractional,
        "promoted_from_run": row.promoted_from_run,
        "activated_at": row.activated_at.isoformat() if row.activated_at else None,
    }
