from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import api_router
from app.api.webhooks import router as webhook_router
from app.config import Settings, get_settings
from app.db import Database
from app.errors import APIError, api_error_handler
from app.providers.base import ProviderError, RateLimitError
from app.utils import iso, utcnow


def create_app(
    settings: Settings | None = None, database: Database | None = None
) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Read-only unified index of repository, Git, release and package metadata.",
    )
    db = database or Database(settings.database_url)
    app.state.settings = settings
    app.state.db = db
    if settings.auto_create_tables:
        db.create_all()

    app.add_exception_handler(APIError, api_error_handler)

    @app.exception_handler(ProviderError)
    async def provider_error_handler(_request: Request, exc: ProviderError) -> JSONResponse:
        status = 429 if isinstance(exc, RateLimitError) else 503
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": "rate_limited" if status == 429 else "provider_error",
                    "message": str(exc),
                    "details": {},
                }
            },
        )

    @app.get("/healthz", tags=["system"])
    def healthz() -> dict:
        return {"status": "ok", "version": __version__, "time": iso(utcnow())}

    app.include_router(api_router)
    app.include_router(webhook_router, prefix="/api/v1")

    frontend = Path(settings.frontend_dir)
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="dashboard")

    return app


app = create_app()
