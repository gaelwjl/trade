"""Alembic environment: runs on the connection passed in by ``store.db.migrate``."""

from alembic import context
from sqlalchemy import create_engine

from tradelab.store.models import Base

config = context.config
target_metadata = Base.metadata


def run() -> None:
    connection = config.attributes.get("connection")
    if (
        connection is None
    ):  # alembic CLI: use the URL from -x url=... or the default file
        from tradelab.store.db import DB_PATH

        url = context.get_x_argument(as_dictionary=True).get(
            "url", f"sqlite:///{DB_PATH}"
        )
        with create_engine(url).connect() as conn:
            _configure_and_run(conn)
            conn.commit()
        return
    _configure_and_run(connection)


def _configure_and_run(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(
        connection=connection, target_metadata=target_metadata, render_as_batch=True
    )
    with context.begin_transaction():
        context.run_migrations()


run()
