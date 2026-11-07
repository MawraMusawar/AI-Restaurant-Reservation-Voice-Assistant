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
EndReason = Literal[
    "caller_hung_up",  # the browser left the call
    "assistant_ended",  # the assistant said goodbye and hung up (end_call tool)
    "caller_silent",  # no answer to the silence check-ins
    "connect_timeout",  # the browser never completed the WebRTC connection
    "error",  # a service failed for good (see `error`)
    "server_shutdown",
]
TranscriptRole = Literal["user", "assistant"]

MAX_RECORDED_ERRORS = 20
LOOP_LAG_INTERVAL_SECS = 0.05
LOOP_STALL_MS = 100.0
# Provider errors can embed long header dumps; keep stored messages readable.
MAX_ERROR_CHARS = 300


@dataclass
class TranscriptEntry:
    role: TranscriptRole
    text: str
    at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # Assistant only: the caller cut in, so `text` is just what was spoken.
    interrupted: bool = False


@dataclass
class ToolCallRecord:
    """One LLM tool call. Arguments are not stored: they contain caller details."""

    name: str
    ok: bool
    error_code: str | None
    duration_ms: float
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
    # The restaurant (database id) the call was for; None without a database.
    restaurant_id: str | None = None
    # Why the call ended (set when it ends).
    end_reason: EndReason | None = None
    # The error that ended (or prevented) the call, if any.
    error: str | None = None
    # Every error reported during the call, including ones that were recovered from.
    errors: list[str] = field(default_factory=list)
    transcript: list[TranscriptEntry] = field(default_factory=list)
    # Time from VAD end-of-speech to the final transcript, per utterance.
    stt_latencies_ms: list[float] = field(default_factory=list)
    # Time from the end of the caller's turn to the assistant's first word of text,
    # per reply (includes turn detection hand-off, tool calls and LLM time-to-first-token).
    response_latencies_ms: list[float] = field(default_factory=list)
    # Time from the end of the caller's turn to the first audio of the reply,
    # i.e. when the caller starts hearing it (adds TTS time-to-first-byte).
    voice_latencies_ms: list[float] = field(default_factory=list)
    # Caller audio discarded by LiveEdgeAudioGate after the call went live, because it
    # arrived faster than real time (the server fell behind). Should stay near 0.
    dropped_audio_secs: float = 0.0
    # Event-loop health during the call: the worst stall, and how many exceeded 100 ms.
    # A stalled loop delays audio both ways (and makes LiveEdgeAudioGate drop audio).
    max_loop_lag_ms: float = 0.0
    loop_stalls: int = 0
    tool_calls: list[ToolCallRecord] = field(default_factory=list)

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

    def add_transcript(self, role: TranscriptRole, text: str, *, interrupted: bool = False) -> None:
        self.transcript.append(TranscriptEntry(role=role, text=text, interrupted=interrupted))

    def record_tool_call(self, name: str, result: dict, duration_ms: float) -> None:
        self.tool_calls.append(
            ToolCallRecord(
                name=name,
                ok=bool(result.get("ok")),
                error_code=result.get("error_code"),
                duration_ms=duration_ms,
            )
        )

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
# Called once a session has finished (e.g. to save the call log).
SessionListener = Callable[[VoiceSession], Awaitable[None]]


class SessionLimitError(Exception):
    """Raised when the maximum number of concurrent sessions is reached."""


class VoiceSessionManager:
    def __init__(
        self,
        bot_runner: BotRunner,
        max_concurrent: int,
        connect_timeout_secs: float = 30.0,
        history_size: int = 50,
        on_finished: SessionListener | None = None,
    ):
        self._bot_runner = bot_runner
        self._on_finished = on_finished
        self._listeners: set[asyncio.Task[None]] = set()
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
        session.end_reason = "connect_timeout"
        logger.warning("Voice session connect timeout | session_id={}", session_id)
        task.cancel()

    async def _run(self, connection: PeerConnection, session: VoiceSession) -> None:
        lag_monitor = asyncio.create_task(_monitor_loop_lag(session))
        try:
            await self._bot_runner(connection, session)
            # The pipeline may end on its own after a fatal service error.
            session.status = "error" if session.error else "ended"
        except asyncio.CancelledError:
            # Cancelled by the connect watchdog (error already set) or by shutdown.
            session.status = "error" if session.error else "ended"
            if session.end_reason is None and not session.error:
                session.end_reason = "server_shutdown"
            raise
        except Exception as exc:
            session.status = "error"
            session.error = f"{type(exc).__name__}: {exc}"
            logger.exception("Voice session failed | session_id={}", session.session_id)
        finally:
            lag_monitor.cancel()
            if session.error and session.end_reason in (None, "caller_hung_up"):
                session.end_reason = "error"
            elif session.end_reason is None:
                session.end_reason = "caller_hung_up"
            watchdog = self._watchdogs.pop(session.session_id, None)
            if watchdog:
                watchdog.cancel()
            session.ended_at = datetime.now(UTC)
            self._active.pop(session.session_id, None)
            self._tasks.pop(session.session_id, None)
            self._history.appendleft(session)
            if self._on_finished is not None:
                listener = asyncio.create_task(self._notify_finished(session))
                self._listeners.add(listener)
                listener.add_done_callback(self._listeners.discard)
            logger.info(
                "Voice session finished | session_id={} status={} end_reason={} duration_s={} "
                "max_loop_lag_ms={} loop_stalls={} dropped_audio_secs={}",
                session.session_id,
                session.status,
                session.end_reason,
                session.duration_seconds,
                session.max_loop_lag_ms,
                session.loop_stalls,
                session.dropped_audio_secs,
            )

    async def _notify_finished(self, session: VoiceSession) -> None:
        assert self._on_finished is not None
        try:
            await self._on_finished(session)
        except Exception:
            # A failed call-log write must never affect calls.
            logger.opt(exception=True).error(
                "Saving finished session failed | session_id={}", session.session_id
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
        if self._listeners:
            # Let call logs of the sessions just ended reach the database.
            await asyncio.gather(*self._listeners, return_exceptions=True)


async def _monitor_loop_lag(session: VoiceSession) -> None:
    """Sample how late the event loop wakes up while the call runs."""
    loop = asyncio.get_running_loop()
    while True:
        started = loop.time()
        await asyncio.sleep(LOOP_LAG_INTERVAL_SECS)
        lag_ms = (loop.time() - started - LOOP_LAG_INTERVAL_SECS) * 1000
        if lag_ms > session.max_loop_lag_ms:
            session.max_loop_lag_ms = round(lag_ms, 1)
        if lag_ms > LOOP_STALL_MS:
            session.loop_stalls += 1
            logger.warning(
                "Event loop stalled {:.0f} ms | session_id={}", lag_ms, session.session_id
            )
