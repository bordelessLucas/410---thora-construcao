import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import abc_stubs, analise, curva_abc, export, health, process, tables, uploads
from app.config import (
    API_DESCRIPTION,
    API_TITLE,
    API_VERSION,
    CORS_ORIGIN_REGEX,
    FRONTEND_URLS,
)

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    application = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=FRONTEND_URLS,
        allow_origin_regex=CORS_ORIGIN_REGEX or None,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(health.router)
    application.include_router(uploads.router)
    application.include_router(tables.router)
    application.include_router(process.router)
    application.include_router(export.router)
    application.include_router(abc_stubs.router)
    application.include_router(analise.router)
    application.include_router(curva_abc.router)

    logger.info("Thora API v%s iniciada", API_VERSION)
    return application


app = create_app()
