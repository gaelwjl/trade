"""Request plumbing shared by the routers."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from ..store.db import session_scope


def db(request: Request) -> Iterator[Session]:
    with session_scope(request.app.state.engine) as s:
        yield s


def iso(v: object) -> object:
    return v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v


def row_dict(row: object, fields: list[str]) -> dict:
    return {f: iso(getattr(row, f)) for f in fields}
