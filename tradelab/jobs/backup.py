"""Nightly SQLite online backups (keep the newest 30) and restore."""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from ..data.prices import DATA_DIR
from ..store.db import DB_PATH

BACKUP_DIR = DATA_DIR / "backups"
KEEP = 30


def backup(db: Path = DB_PATH, dest_dir: Path = BACKUP_DIR, keep: int = KEEP) -> Path:
    """Consistent copy of a live database (SQLite online backup API)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"tradelab-{stamp}.sqlite"
    with sqlite3.connect(db) as src, sqlite3.connect(dest) as out:
        src.backup(out)
    for old in sorted(dest_dir.glob("tradelab-*.sqlite"))[:-keep]:
        old.unlink()
    return dest


def restore(backup_file: Path, db: Path = DB_PATH) -> None:
    """Replace ``db`` with ``backup_file``; the current database is kept beside it first."""
    if db.exists():
        keep = db.with_suffix(
            f".before-restore-{dt.datetime.now():%Y%m%d-%H%M%S}.sqlite"
        )
        with sqlite3.connect(db) as src, sqlite3.connect(keep) as out:
            src.backup(out)
    with sqlite3.connect(backup_file) as src, sqlite3.connect(db) as out:
        src.backup(out)
