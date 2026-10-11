"""The web app: JSON API under /api plus the HTML pages (``tradelab serve``)."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from ..live.portfolio import LedgerError
from ..store.db import open_db
from . import backtests, portfolio, strategy, system


def create_app(engine: Engine | None = None) -> FastAPI:
    app = FastAPI(
        title="tradelab",
        description="Portfolio, daily strategy signals and batch backtests (single user, local).",
    )
    app.state.engine = engine or open_db()

    @app.exception_handler(LedgerError)
    @app.exception_handler(ValueError)
    async def bad_request(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(KeyError)
    async def not_found(_: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(
            {"detail": f"not found: {exc.args[0] if exc.args else ''}"}, status_code=404
        )

    for module in (portfolio, strategy, backtests, system):
        app.include_router(module.router, prefix="/api")

    from ..web import (
        pages,
    )  # imported late: pages depend on the API's session dependency

    app.include_router(pages.router)
    return app
