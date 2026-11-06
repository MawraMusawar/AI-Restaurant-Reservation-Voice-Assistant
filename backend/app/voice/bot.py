"""The Pipecat voice pipeline for one call.

Milestone 3 pipeline (the caller speaks, the assistant replies in text):

    browser mic -> SmallWebRTC input -> live-edge gate (drops stale backlogs)
                -> Deepgram STT (streaming) -> session recorder (ready, STT latency)
                -> user aggregator (Silero VAD + smart-turn end-of-turn detection)
                -> Groq LLM (tool calling, fails over between free models)
                -> response latency recorder -> SmallWebRTC output
                -> assistant aggregator (adds replies and tool results to the context)

The user aggregator runs the VAD and sends its speech events upstream too, so
Deepgram is still asked to finalize the moment the caller stops speaking.
Transcripts reach the browser through RTVI (`user-input`). Assistant replies
are sent as RTVI server messages (`assistant-message`) until Milestone 4 adds
ElevenLabs TTS and speaks them.
"""

from typing import Any

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import ErrorFrame
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
from pipecat.utils.errors import ErrorCategory
from pipecat.workers.runner import WorkerRunner

from app.agent.prompt import build_greeting, build_system_prompt
from app.agent.reservations import OfflineReservationBackend
from app.agent.restaurant import DEMO_RESTAURANT
from app.agent.tools import ReservationTools
from app.config import get_settings
from app.services.llm import create_llm_service
from app.services.stt import create_stt_service
from app.voice.processors import LiveEdgeAudioGate, ResponseLatencyRecorder, SessionRecorder
from app.voice.sessions import VoiceSession

# End the call if nothing happens (no speech, no transcripts) for this long.
IDLE_TIMEOUT_SECS = 300


def assistant_message(text: str) -> dict[str, Any]:
    return {"type": "assistant-message", "text": text}


async def run_voice_bot(connection: SmallWebRTCConnection, session: VoiceSession) -> None:
    settings = get_settings()
    profile = DEMO_RESTAURANT

    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_10ms_chunks=2,
        ),
    )
    stt = create_stt_service(settings)

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

    tools = ReservationTools(
        profile, OfflineReservationBackend(), observer=session.record_tool_call
    )
    greeting = build_greeting(profile)
    context = LLMContext(
        messages=[{"role": "assistant", "content": greeting}],
        tools=tools.schema(),
    )
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )
    response_latency = ResponseLatencyRecorder(session)

    pipeline = Pipeline(
        [
            transport.input(),
            LiveEdgeAudioGate(),
            stt,
            SessionRecorder(session),
            user_aggregator,
            llm,
            response_latency,
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
        if frame.processor is llm and not fatal:
            # The call continues, but this turn got no answer. Tell the browser why.
            code = "rate_limited" if frame.category == ErrorCategory.RATE_LIMIT else "llm_error"
            await worker.rtvi.send_server_message({"type": "assistant-error", "code": code})

    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(_rtvi: RTVIProcessor) -> None:
        # Registered after the worker's own handler, so this follows `bot-ready`.
        session.add_transcript("assistant", greeting)
        await worker.rtvi.send_server_message(assistant_message(greeting))

    @user_aggregator.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(_aggregator, _strategy, message: UserTurnStoppedMessage) -> None:
        if message.content:
            response_latency.mark_user_turn_end()
            session.add_transcript("user", message.content)
            logger.debug("Caller turn: {!r}", message.content)

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(_aggregator, message: AssistantTurnStoppedMessage) -> None:
        text = message.content.strip()
        if text:
            session.add_transcript("assistant", text)
            await worker.rtvi.send_server_message(assistant_message(text))
            logger.debug("Assistant reply: {!r}", text)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _connection) -> None:
        session.mark_active()
        logger.info("Client connected | session_id={}", session.session_id)

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _connection) -> None:
        logger.info("Client disconnected | session_id={}", session.session_id)
        await runner.cancel(reason="client disconnected")

    await runner.add_workers(worker)
    await runner.run()
