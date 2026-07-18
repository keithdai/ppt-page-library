from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from pptlib import __version__
from pptlib.application.library import (
    InMemorySelectionStore,
    InMemorySlideCatalog,
    PageLibraryService,
    SelectionStore,
    SlideCatalog,
)
from pptlib.application.render_assets import backfill_thumbnails
from pptlib.config import Settings, load_settings
from pptlib.domain.ids import new_id
from pptlib.infrastructure.db.connection import connect
from pptlib.infrastructure.db.library import SqliteSelectionStore, SqliteSlideCatalog
from pptlib.web.errors import install_error_handlers
from pptlib.web.routes.api import router as api_router
from pptlib.web.routes.pages import router as page_router

logger = logging.getLogger("pptlib.web")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    async def run_backfill() -> None:
        try:
            await asyncio.to_thread(backfill_thumbnails, app.state.settings)
        except Exception:
            logger.exception("thumbnail backfill failed")

    task = asyncio.create_task(run_backfill())
    app.state.thumbnail_backfill_task = task
    try:
        yield
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def create_app(
    settings: Settings | None = None,
    *,
    catalog: SlideCatalog | None = None,
    selection: SelectionStore | None = None,
) -> FastAPI:
    app = FastAPI(title="PPT Page Library", version=__version__, lifespan=_lifespan)
    app.state.settings = settings or load_settings()
    if catalog is None:
        catalog = _default_catalog(app.state.settings)
    if selection is None:
        selection = _default_selection(app.state.settings)
    app.state.library = PageLibraryService(catalog, selection)
    static_dir = Path(__file__).resolve().parent / "static"
    app.state.settings.assets_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.mount("/assets", StaticFiles(directory=app.state.settings.assets_dir), name="assets")
    install_error_handlers(app)

    @app.middleware("http")
    async def request_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_id = new_id("req")
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    app.include_router(page_router)
    app.include_router(api_router)
    return app


def _table_exists(database_path: Path, table: str) -> bool:
    try:
        connection = connect(database_path)
        try:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
                (table,),
            ).fetchone()
            return row is not None
        finally:
            connection.close()
    except sqlite3.Error:
        return False


def _default_catalog(settings: Settings) -> SlideCatalog:
    if _table_exists(settings.database_path, "slides"):
        return SqliteSlideCatalog(settings.database_path, assets_dir=settings.assets_dir)
    return InMemorySlideCatalog()


def _default_selection(settings: Settings) -> SelectionStore:
    if _table_exists(settings.database_path, "selections"):
        return SqliteSelectionStore(settings.database_path, assets_dir=settings.assets_dir)
    return InMemorySelectionStore()
