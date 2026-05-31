"""FastAPI application entry point."""
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.errors import AppException, ErrorResponse, ErrorCodes
from app.core.logging import logger
from app.routers import (
    videos_router,
    auth_router,
    failures_router,
    feedback_router,
    sops_router,
)


async def _reset_stuck_pipelines() -> None:
    """Mark in-flight pipeline rows as failed when the API restarts."""
    from sqlalchemy import text
    from app.models.base import get_async_engine

    engine = get_async_engine()
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(
                "UPDATE videos "
                "SET status = 'failed', updated_at = CURRENT_TIMESTAMP "
                "WHERE status IN ('transcribing', 'sop_generating')"
            ))
            count = getattr(result, "rowcount", None)
            if count and count > 0:
                logger.info(f"[startup] reset {count} stuck pipeline row(s) -> failed")
    except Exception as e:
        logger.warning(f"[startup] reset_stuck_pipelines failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info(f"Starting {settings.app_name}")
    from app.models import Base  # noqa: ensure all models are registered
    from app.models.base import init_db
    try:
        await init_db()
        logger.info(f"Database initialized ({settings.database_url.split('://', 1)[0]})")
    except Exception as e:
        logger.error(f"init_db failed: {e}")
        raise

    try:
        from app.services.bootstrap_service import bootstrap_superadmin
        await bootstrap_superadmin()
    except Exception as e:
        logger.warning(f"[startup] superadmin bootstrap failed: {e}")

    await _reset_stuck_pipelines()

    try:
        from app.services.cleanup_service import cleanup_stale_processing_files
        cleanup_stale_processing_files()
    except Exception as e:
        logger.warning(f"[startup] stale cleanup failed: {e}")

    yield
    logger.info(f"Shutting down {settings.app_name}")
    try:
        from app.models.base import dispose_engine
        await dispose_engine()
    except Exception as e:
        logger.warning(f"[shutdown] dispose_engine failed: {e}")


app = FastAPI(
    title=settings.app_name,
    description="Video to SOP generation pipeline",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    logger.warning(f"AppException: {exc.error_code} - {exc.message}")
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_response().model_dump(mode="json")
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(f"Unexpected error: {str(exc)}", exc_info=True)
    error_response = ErrorResponse(
        error_code=ErrorCodes.INTERNAL_ERROR,
        message="An unexpected error occurred",
        details={"error": str(exc)} if settings.debug else None,
        timestamp=datetime.utcnow()
    )
    return JSONResponse(
        status_code=500,
        content=error_response.model_dump(mode="json")
    )


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "app_name": settings.app_name,
        "environment": settings.app_env
    }


@app.get("/")
async def root():
    return {
        "message": f"Welcome to {settings.app_name}",
        "docs": "/docs",
        "health": "/health"
    }


app.include_router(videos_router)
app.include_router(auth_router)
app.include_router(failures_router)
app.include_router(feedback_router)
app.include_router(sops_router)
