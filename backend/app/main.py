"""FastAPI application entry point.

Run locally:  uv run uvicorn app.main:app --reload --port 8000
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequestHandler

from app import __version__
from app.api.health import router as health_router
from app.config import get_settings
from app.logging_setup import configure_logging
from app.voice.bot import run_voice_bot
from app.voice.router import VoiceServices
from app.voice.router import router as voice_router
from app.voice.sessions import VoiceSessionManager


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info(
        "Backend starting | env={} integrations={}",
        settings.app_env,
        settings.integration_status(),
    )
    voice = VoiceServices(
        webrtc=SmallWebRTCRequestHandler(),
        sessions=VoiceSessionManager(
            bot_runner=run_voice_bot,
            max_concurrent=settings.voice_max_concurrent_sessions,
        ),
    )
    app.state.voice = voice
    try:
        yield
    finally:
        logger.info("Backend shutting down")
        await voice.sessions.shutdown()
        await voice.webrtc.close()


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
    app.include_router(health_router, prefix="/api")
    app.include_router(voice_router, prefix="/api")
    return app


app = create_app()
