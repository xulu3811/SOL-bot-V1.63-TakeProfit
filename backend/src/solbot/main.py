"""ASGI entry point; exactly one database owner and collector per process."""

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
import logging
import sqlite3
from typing import Any

from fastapi import FastAPI

from solbot.api.routes import router
from solbot.config import Settings
from solbot.market.collector import Collector
from solbot.storage.database import Database, DatabaseUnavailable

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    database_factory: Callable[..., Any] = Database,
    collector_factory: Callable[..., Any] = Collector,
) -> FastAPI:
    configuration = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        # Preserve the P1 injectable factory signature for the SOL default.
        database = (database_factory(configuration.db_path) if configuration.symbol == "SOLUSDT"
                    else database_factory(configuration.db_path, symbol=configuration.symbol))
        application.state.settings = configuration
        application.state.database = database
        application.state.database_available = False
        application.state.collector = None
        application.state.collector_task = None
        try:
            try:
                await database.start()
                application.state.database_available = True
            except (DatabaseUnavailable, sqlite3.Error, OSError):
                # Keep the loopback health endpoint available with explicit 503.
                logger.exception("Market database startup failed")
            if application.state.database_available:
                collector = collector_factory(database)
                application.state.collector = collector
                application.state.collector_task = asyncio.create_task(
                    collector.run(), name="solbot-market-collector"
                )
            yield
        finally:
            task = application.state.collector_task
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    try:
                        await task
                    except Exception:
                        logger.exception("Market collector stopped with an error")
            application.state.database_available = False
            try:
                await database.close()
            except (DatabaseUnavailable, sqlite3.Error, OSError):
                logger.exception("Market database shutdown failed")

    application = FastAPI(
        title=f"{configuration.symbol} public market data", version="0.2.0", lifespan=lifespan,
        docs_url=None, redoc_url=None,
    )
    application.state.settings = configuration
    application.include_router(router)
    return application


app = create_app()


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(app, host="127.0.0.1", port=app.state.settings.port, workers=1, proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()
