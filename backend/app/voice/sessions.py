"""Voice session lifecycle management.

Each browser call is one WebRTC peer connection running one Pipecat pipeline
in its own asyncio task. This module tracks those sessions, enforces a
concurrency limit (free API quotas are small), and makes sure every task is
cancelled when the server shuts down.

Sessions are kept in memory for now. Milestone 6 persists them in the
`voice_sessions` table.
"""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

from loguru import logger

SessionStatus = Literal["connecting", "active", "ended", "error"]
TranscriptRole = Literal["user", "assistant"]

MAX_RECORDED_ERRORS = 20
# Provider errors can embed long header dumps; keep stored messages readable.
MAX_ERROR_CHARS = 300


@dataclass
class TranscriptEntry:
    role: TranscriptRole
    text: str
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


class PeerConnection(Protocol):
    """The part of `SmallWebRTCConnection` the session manager relies on."""

    @property
    def pc_id(self) -> str: ...


@dataclass
class VoiceSession:
    session_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    connected_at: datetime | None = None
    # When every service finished setup and the pipeline started (the call is live).
    ready_at: datetime | None = None
    ended_at: datetime | None = None
    status: SessionStatus = "connecting"
    # The error that ended (or prevented) the call, if any.
    error: str | None = None
    # Every error reported during the call, including ones that were recovered from.
    errors: list[str] = field(default_factory=list)
    transcript: list[TranscriptEntry] = field(default_factory=list)
    # Time from VAD end-of-speech to the final transcript, per utterance.
    stt_latencies_ms: list[float] = field(default_factory=list)

    def mark_active(self) -> None:
        if self.status == "connecting":
            self.status = "active"
            self.connected_at = datetime.now(UTC)

    def mark_ready(self) -> None:
        if self.ready_at is None:
            self.ready_at = datetime.now(UTC)

    @property
    def startup_ms(self) -> float | None:
        """Offer received -> pipeline live (dominated by the STT WebSocket handshake)."""
        if self.ready_at is None:
            return None
        return round((self.ready_at - self.started_at).total_seconds() * 1000, 1)

    def add_transcript(self, role: TranscriptRole, text: str) -> None:
        self.transcript.append(TranscriptEntry(role=role, text=text))

    def record_error(self, message: str, *, fatal: bool) -> None:
        if len(message) > MAX_ERROR_CHARS:
            message = message[: MAX_ERROR_CHARS - 1] + "…"
        if len(self.errors) < MAX_RECORDED_ERRORS:
            self.errors.append(message)
        if fatal and self.error is None:
            self.error = message

    @property
    def duration_seconds(self) -> float | None:
        if self.connected_at is None:
            return None
        end = self.ended_at or datetime.now(UTC)
        return round((end - self.connected_at).total_seconds(), 1)


BotRunner = Callable[[PeerConnection, VoiceSession], Awaitable[None]]


class SessionLimitError(Exception):
    """Raised when the maximum number of concurrent sessions is reached."""


class VoiceSessionManager:
    def __init__(
        self,
        bot_runner: BotRunner,
        max_concurrent: int,
        connect_timeout_secs: float = 30.0,
        history_size: int = 50,
    ):
        self._bot_runner = bot_runner
        self._max_concurrent = max_concurrent
        self._connect_timeout_secs = connect_timeout_secs
        self._active: dict[str, VoiceSession] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._watchdogs: dict[str, asyncio.TimerHandle] = {}
        self._history: deque[VoiceSession] = deque(maxlen=history_size)

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    def has_capacity(self) -> bool:
        return self.active_count < self._max_concurrent

    def is_active(self, session_id: str) -> bool:
        return session_id in self._active

    def start(self, connection: PeerConnection) -> VoiceSession:
        """Start the voice pipeline for a new peer connection in a background task."""
        if not self.has_capacity():
            raise SessionLimitError(f"Maximum of {self._max_concurrent} concurrent sessions")
        session = VoiceSession(session_id=connection.pc_id)
        self._active[session.session_id] = session
        self._tasks[session.session_id] = asyncio.create_task(
            self._run(connection, session), name=f"voice-session-{session.session_id}"
        )
        self._watchdogs[session.session_id] = asyncio.get_running_loop().call_later(
            self._connect_timeout_secs, self._on_connect_timeout, session.session_id
        )
        logger.info("Voice session started | session_id={}", session.session_id)
        return session

    def _on_connect_timeout(self, session_id: str) -> None:
        """End a session whose browser never completed the WebRTC connection."""
        session = self._active.get(session_id)
        task = self._tasks.get(session_id)
        if session is None or task is None or session.status != "connecting":
            return
        session.error = f"Client did not connect within {self._connect_timeout_secs:g}s"
        logger.warning("Voice session connect timeout | session_id={}", session_id)
        task.cancel()

    async def _run(self, connection: PeerConnection, session: VoiceSession) -> None:
        try:
            await self._bot_runner(connection, session)
            # The pipeline may end on its own after a fatal service error.
            session.status = "error" if session.error else "ended"
        except asyncio.CancelledError:
            # Cancelled by the connect watchdog (error already set) or by shutdown.
            session.status = "error" if session.error else "ended"
            raise
        except Exception as exc:
            session.status = "error"
            session.error = f"{type(exc).__name__}: {exc}"
            logger.exception("Voice session failed | session_id={}", session.session_id)
        finally:
            watchdog = self._watchdogs.pop(session.session_id, None)
            if watchdog:
                watchdog.cancel()
            session.ended_at = datetime.now(UTC)
            self._active.pop(session.session_id, None)
            self._tasks.pop(session.session_id, None)
            self._history.appendleft(session)
            logger.info(
                "Voice session finished | session_id={} status={} duration_s={}",
                session.session_id,
                session.status,
                session.duration_seconds,
            )

    def list_sessions(self) -> list[VoiceSession]:
        """Active sessions first, then the most recent finished ones."""
        return [*self._active.values(), *self._history]

    async def shutdown(self) -> None:
        """Cancel every running session. Called from the FastAPI lifespan."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("Cancelled {} voice session(s) on shutdown", len(tasks))
