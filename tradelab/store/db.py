"""SQLite engine, sessions and migrations. The database lives in data/ (git-ignored)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ..data.prices import DATA_DIR

DB_PATH = DATA_DIR / "tradelab.sqlite"
MIGRATIONS = Path(__file__).parent / "migrations"


def make_engine(path: Path | str | None = None) -> Engine:
    """``None`` = the default file; ``":memory:"`` = a private in-memory database (tests)."""
    if path == ":memory:":
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        file = Path(path) if path else DB_PATH
        file.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            f"sqlite:///{file}", connect_args={"check_same_thread": False}
        )

    @event.listens_for(engine, "connect")
    def _pragmas(conn, _record) -> None:  # type: ignore[no-untyped-def]
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def migrate(engine: Engine) -> None:
    """Bring the schema to the latest migration."""
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def open_db(path: Path | str | None = None) -> Engine:
    engine = make_engine(path)
    migrate(engine)
    return engine


T = TypeVar("T")


def must_get(s: Session, model: type[T], key: Any) -> T:
    """``Session.get`` that raises ``KeyError`` (an HTTP 404 in the API) when the row is missing."""
    row = s.get(model, key)
    if row is None:
        raise KeyError(f"{model.__name__} {key}")
    return row


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """One unit of work: commits on success, rolls back on error."""
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
