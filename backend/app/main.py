"""FastAPI application entry point.

Run locally:  uv run uvicorn app.main:app --reload --port 8000
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

import asyncpg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequestHandler

from app import __version__
from app.api.auth import router as auth_router
from app.api.calls import router as calls_router
from app.api.dashboard import router as dashboard_router
from app.api.health import router as health_router
from app.api.reservations import router as reservations_router
from app.api.restaurant_settings import router as settings_router
from app.config import Settings, get_settings
from app.db import Database, create_database
from app.db.sessions import save_session
from app.logging_setup import configure_logging
from app.voice.bot import run_voice_bot, warm_up_voice_stack
from app.voice.router import VoiceServices
from app.voice.router import router as voice_router
from app.voice.sessions import VoiceSession, VoiceSessionManager


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info(
        "Backend starting | env={} integrations={}",
        settings.app_env,
        settings.integration_status(),
    )
    db = await _open_database(settings)
    app.state.db = db

    async def save_call_log(session: VoiceSession) -> None:
        if db is not None:
            await save_session(db.pool, session, session.restaurant_id)

    voice = VoiceServices(
        webrtc=SmallWebRTCRequestHandler(),
        sessions=VoiceSessionManager(
            bot_runner=partial(run_voice_bot, db=db),
            max_concurrent=settings.voice_max_concurrent_sessions,
            on_finished=save_call_log,
        ),
    )
    app.state.voice = voice
    warm_up = asyncio.create_task(_warm_up(settings))
    try:
        yield
    finally:
        logger.info("Backend shutting down")
        warm_up.cancel()
        await voice.sessions.shutdown()
        await voice.webrtc.close()
        if db is not None:
            await db.close()


async def _open_database(settings: Settings) -> Database | None:
    """Connect if DATABASE_URL is set. Failure is logged, not fatal: calls still work,
    but bookings report the reservation system as offline."""
    if settings.database_url is None:
        logger.warning("DATABASE_URL not set: bookings are offline")
        return None
    try:
        return await create_database(settings)
    except Exception:
        logger.opt(exception=True).error("Database unavailable: bookings are offline")
        return None


async def _warm_up(settings: Settings) -> None:
    try:
        await asyncio.to_thread(warm_up_voice_stack, settings)
    except Exception:
        # Not fatal: the first call just does this work itself.
        logger.opt(exception=True).warning("Voice stack warm-up failed")


async def _database_error(_request: Request, exc: Exception) -> JSONResponse:
    """Database errors in management requests: constraint violations are the client's
    fault (409/422); anything else means the database is unavailable (503)."""
    if isinstance(exc, asyncpg.exceptions.UniqueViolationError):
        return JSONResponse({"detail": "That already exists."}, status_code=409)
    if isinstance(exc, asyncpg.exceptions.IntegrityConstraintViolationError):
        return JSONResponse({"detail": "That change violates a data rule."}, status_code=422)
    logger.opt(exception=exc).error("Database error in request")
    return JSONResponse(
        {"detail": "The reservation database is unavailable. Try again shortly."},
        status_code=503,
    )


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)

    app = FastAPI(
        title="AI Restaurant Reservation Voice Assistant",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    for router in (
        health_router,
        voice_router,
        auth_router,
        dashboard_router,
        reservations_router,
        settings_router,
        calls_router,
    ):
        app.include_router(router, prefix="/api")
    app.add_exception_handler(asyncpg.PostgresError, _database_error)
    app.add_exception_handler(asyncpg.InterfaceError, _database_error)
    app.add_exception_handler(OSError, _database_error)
    return app


app = create_app()
