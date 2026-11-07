"""The Pipecat voice pipeline for one call.

Milestone 4 pipeline (a spoken conversation):

    browser mic -> SmallWebRTC input -> live-edge gate (drops stale backlogs)
                -> Deepgram STT (streaming) -> session recorder (ready, STT latency)
                -> user aggregator (Silero VAD + smart-turn end-of-turn detection)
                -> Groq LLM (tool calling, fails over between free models)
                -> text latency probe -> ElevenLabs TTS (streaming WebSocket)
                -> audio latency probe -> SmallWebRTC output (speaker)
                -> assistant aggregator (adds what was spoken and tool results to the context)

The user aggregator runs the VAD and sends its speech events upstream too, so
Deepgram is still asked to finalize the moment the caller stops speaking.
When the caller starts speaking while the assistant talks, the aggregator
broadcasts an interruption: the LLM response and TTS audio are cancelled, queued
audio is dropped, and only the words actually spoken stay in the context.

The browser gets RTVI messages: `user-input` (caller transcript), `bot-output`
(each assistant word as it is spoken), speaking/interruption events, tool
activity (names only), and `assistant-error` server messages.
"""

import asyncio
import time

from loguru import logger
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import EndWorkerFrame, ErrorFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    AssistantTurnStoppedMessage,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
    UserTurnStoppedMessage,
)
from pipecat.processors.frameworks.rtvi.observer import (
    RTVIFunctionCallReportLevel,
    RTVIObserverParams,
)
from pipecat.processors.frameworks.rtvi.processor import RTVIProcessor
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.turns.user_start import MinWordsUserTurnStartStrategy, VADUserTurnStartStrategy
from pipecat.turns.user_stop import (
    SpeechTimeoutUserTurnStopStrategy,
    TurnAnalyzerUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.utils.errors import ErrorCategory
from pipecat.workers.runner import WorkerRunner

from app.agent.call_control import (
    LLM_TROUBLE_REPLY,
    SilenceTracker,
    closing_frames,
    end_call_tool,
)
from app.agent.prompt import build_greeting, build_system_prompt
from app.agent.reservations import OfflineReservationBackend, ReservationBackend
from app.agent.restaurant import DEMO_RESTAURANT, RestaurantProfile
from app.agent.tools import ReservationTools
from app.config import Settings, get_settings
from app.db import Database
from app.db.reservations import DB_ERRORS, PostgresReservationBackend
from app.db.restaurants import RestaurantNotFoundError, load_profile
from app.services import missing_voice_credentials
from app.services.llm import create_llm_service
from app.services.stt import create_stt_service
from app.services.tts import create_tts_service
from app.voice.processors import (
    LiveEdgeAudioGate,
    ReplyLatencyProbe,
    SessionRecorder,
    TurnClock,
    is_audio,
    is_text,
)
from app.voice.sessions import VoiceSession

# End the call if nothing happens (no speech, no transcripts) for this long.
IDLE_TIMEOUT_SECS = 300
# Barge-in (settings.voice_vad_start_secs / voice_barge_in_min_words): the caller's
# turn, and so an interruption, starts on whichever comes first:
# - VAD: that much continuous voice;
# - words: while the assistant speaks, that many transcribed words (one when it is
#   silent). Measured: words alone delayed barge-in to ~2.3 s (Deepgram interims).


async def run_voice_bot(
    connection: SmallWebRTCConnection, session: VoiceSession, db: Database | None = None
) -> None:
    settings = get_settings()
    profile, backend = await _restaurant_and_backend(db, session)

    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_10ms_chunks=2,
        ),
    )
    stt = create_stt_service(settings)
    tts = create_tts_service(settings, voice_id=profile.voice_id)

    def on_llm_failover(model: str, next_model: str, exc: Exception) -> None:
        session.record_error(
            f"LLM {model} unavailable ({type(exc).__name__}); answered with {next_model}",
            fatal=False,
        )

    llm = create_llm_service(
        settings,
        system_instruction=build_system_prompt(profile, profile.now()),
        on_failover=on_llm_failover,
    )

    async def end_call() -> None:
        # Pushed behind the goodbye already queued for speech, so it is heard first.
        session.end_reason = "assistant_ended"
        await worker.rtvi.send_server_message({"type": "call-ending", "reason": "assistant_ended"})
        for frame in closing_frames(said_goodbye=text_probe.replied_to_current_turn):
            await llm.push_frame(frame)

    tools = ReservationTools(profile, backend, observer=session.record_tool_call)
    greeting = build_greeting(profile)
    # The greeting enters the context when it is spoken (see on_client_ready).
    context = LLMContext(messages=[], tools=tools.schema(extra=[end_call_tool(end_call)]))
    # Loading the ONNX models (Silero VAD, smart-turn) takes ~0.5 s of CPU. Done on the
    # event loop it froze every call in progress (measured stalls up to 0.67 s), so
    # build them in a worker thread.
    user_params = await asyncio.to_thread(_user_aggregator_params, settings)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context, user_params=user_params
    )
    silence = SilenceTracker()
    turn_clock = TurnClock()
    text_probe = ReplyLatencyProbe(turn_clock, session.response_latencies_ms, "text", is_text)

    pipeline = Pipeline(
        [
            transport.input(),
            LiveEdgeAudioGate(session=session),
            stt,
            SessionRecorder(session),
            user_aggregator,
            llm,
            text_probe,
            tts,
            ReplyLatencyProbe(turn_clock, session.voice_latencies_ms, "audio", is_audio),
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True),
        idle_timeout_secs=IDLE_TIMEOUT_SECS,
        conversation_id=session.session_id,
        # If a service fails for good (invalid key), end the call instead of
        # keeping a receptionist on the line that cannot hear or answer.
        processor_unusable_policy=ProcessorUnusablePolicy.END,
        # Tool names only (for "Checking availability..."), never arguments or results.
        rtvi_observer_params=RTVIObserverParams(
            function_call_report_level={"*": RTVIFunctionCallReportLevel.NAME}
        ),
    )
    runner = WorkerRunner(handle_sigint=False)

    @worker.event_handler("on_pipeline_error")
    async def on_pipeline_error(_worker, frame: ErrorFrame) -> None:
        fatal = bool(frame.fatal or (frame.processor is not None and not frame.processor.is_usable))
        source = frame.processor.name if frame.processor is not None else "pipeline"
        session.record_error(f"{source}: {frame.error}", fatal=fatal)
        log = logger.error if fatal else logger.warning
        log(
            "Pipeline error | session_id={} fatal={} {}: {}",
            session.session_id,
            fatal,
            source,
            frame.error,
        )
        if not fatal and frame.processor is llm:
            # Don't leave the caller in silence: apologise out loud (no LLM needed).
            await worker.queue_frames([TTSSpeakFrame(LLM_TROUBLE_REPLY)])
        if not fatal and frame.processor in (llm, tts):
            # The call continues, but this turn got no (spoken) answer. Tell the browser why.
            await worker.rtvi.send_server_message(
                {
                    "type": "assistant-error",
                    "code": _error_code(frame, is_llm=frame.processor is llm),
                }
            )

    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(_rtvi: RTVIProcessor) -> None:
        # Registered after the worker's own handler, so this follows `bot-ready`.
        # Spoken straight away: a fixed greeting needs no LLM request.
        await worker.queue_frames([TTSSpeakFrame(greeting)])

    @user_aggregator.event_handler("on_user_turn_started")
    async def on_user_turn_started(_aggregator, _strategy) -> None:
        silence.reset()

    @user_aggregator.event_handler("on_user_turn_idle")
    async def on_user_turn_idle(_aggregator) -> None:
        step = silence.next_prompt()
        if step is None:
            return
        text, hang_up = step
        logger.info("Caller silent | session_id={} hang_up={}", session.session_id, hang_up)
        frames = [TTSSpeakFrame(text)]
        if hang_up:
            session.end_reason = "caller_silent"
            await worker.rtvi.send_server_message(
                {"type": "call-ending", "reason": "caller_silent"}
            )
            frames.append(EndWorkerFrame())  # after the goodbye has been spoken
        await worker.queue_frames(frames)

    @user_aggregator.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(_aggregator, _strategy, message: UserTurnStoppedMessage) -> None:
        if message.content:
            turn_clock.mark_user_turn_end()
            session.add_transcript("user", message.content)
            logger.debug("Caller turn: {!r}", message.content)

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(_aggregator, message: AssistantTurnStoppedMessage) -> None:
        # With TTS this is what was actually spoken; cut short if the caller interrupted.
        text = message.content.strip()
        if text:
            session.add_transcript("assistant", text, interrupted=message.interrupted)
            logger.debug("Assistant said (interrupted={}): {!r}", message.interrupted, text)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _connection) -> None:
        session.mark_active()
        logger.info("Client connected | session_id={}", session.session_id)

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _connection) -> None:
        logger.info("Client disconnected | session_id={}", session.session_id)
        if session.end_reason is None:
            session.end_reason = "caller_hung_up"
        await runner.cancel(reason="client disconnected")

    await runner.add_workers(worker)
    await runner.run()


async def _restaurant_and_backend(
    db: Database | None, session: VoiceSession
) -> tuple[RestaurantProfile, ReservationBackend]:
    """The restaurant profile and the reservation backend for this call.

    Without a working database the call still runs, using the built-in demo
    profile and the offline backend, which never reports availability or bookings.
    """
    if db is None:
        return DEMO_RESTAURANT, OfflineReservationBackend()
    try:
        async with db.pool.acquire() as conn:
            profile = await load_profile(conn, db.restaurant_slug)
    except (RestaurantNotFoundError, *DB_ERRORS) as exc:
        session.record_error(f"database: {exc}", fatal=False)
        logger.opt(exception=True).error("Could not load the restaurant; bookings are offline")
        return DEMO_RESTAURANT, OfflineReservationBackend()
    session.restaurant_id = profile.id
    return profile, PostgresReservationBackend(db.pool, profile, session.session_id)


def warm_up_voice_stack(settings: Settings) -> None:
    """Do the slow one-time work of a first call at server start, in a worker thread.

    The first call after a (re)start built Pydantic schemas, ONNX models and TLS
    clients on the event loop: measured stalls of 6-9 s on this machine. Building
    the same objects once here leaves only per-call work for real calls.
    """
    started = time.perf_counter()
    params = _user_aggregator_params(settings)  # Silero VAD + smart-turn ONNX models
    LLMContextAggregatorPair(LLMContext(messages=[]), user_params=params)
    if not missing_voice_credentials(settings):
        create_stt_service(settings)
        create_tts_service(settings)
        create_llm_service(settings, system_instruction="warm-up")
    logger.info("Voice stack warmed up in {:.1f}s", time.perf_counter() - started)


def _user_aggregator_params(settings: Settings) -> LLMUserAggregatorParams:
    return LLMUserAggregatorParams(
        vad_analyzer=SileroVADAnalyzer(params=VADParams(start_secs=settings.voice_vad_start_secs)),
        # Start: sustained voice or enough words (see above).
        # Stop: both must agree (stop mode ALL): the smart-turn model judges the
        # sentence complete, and the caller has been quiet for voice_turn_end_silence_secs.
        user_turn_strategies=UserTurnStrategies(
            start=[
                VADUserTurnStartStrategy(),
                MinWordsUserTurnStartStrategy(min_words=settings.voice_barge_in_min_words),
            ],
            stop=[
                TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3()),
                SpeechTimeoutUserTurnStopStrategy(
                    user_speech_timeout=settings.voice_turn_end_silence_secs
                ),
            ],
        ),
        # Fires when the caller stays silent after the assistant finished speaking.
        user_idle_timeout=settings.voice_silence_prompt_secs,
    )


def _error_code(frame: ErrorFrame, *, is_llm: bool) -> str:
    if is_llm:
        return "rate_limited" if frame.category == ErrorCategory.RATE_LIMIT else "llm_error"
    return "tts_quota" if frame.category == ErrorCategory.QUOTA else "tts_error"
