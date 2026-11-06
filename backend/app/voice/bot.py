"""The Pipecat voice pipeline for one call.

Milestone 2 pipeline (live transcription):

    browser mic -> SmallWebRTC input -> live-edge gate (drops stale backlogs)
                -> Silero VAD -> speaking events -> Deepgram STT (streaming)
                -> session recorder (ready, transcript, latency) -> SmallWebRTC output

Interim and final transcripts reach the browser automatically through RTVI
(`user-input` messages). Groq (Milestone 3) and ElevenLabs (Milestone 4) are
added after the STT stage.
"""

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import ErrorFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

from app.config import get_settings
from app.services.stt import create_stt_service
from app.voice.processors import LiveEdgeAudioGate, SessionRecorder, VADSpeakingBridge
from app.voice.sessions import VoiceSession

# End the call if nothing happens (no speech, no transcripts) for this long.
IDLE_TIMEOUT_SECS = 300


async def run_voice_bot(connection: SmallWebRTCConnection, session: VoiceSession) -> None:
    settings = get_settings()

    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_10ms_chunks=2,
        ),
    )
    stt = create_stt_service(settings)

    pipeline = Pipeline(
        [
            transport.input(),
            LiveEdgeAudioGate(),
            VADProcessor(vad_analyzer=SileroVADAnalyzer()),
            VADSpeakingBridge(),
            stt,
            SessionRecorder(session),
            transport.output(),
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True),
        idle_timeout_secs=IDLE_TIMEOUT_SECS,
        conversation_id=session.session_id,
        # If a service fails for good (invalid key, exhausted quota), end the
        # call instead of keeping a receptionist on the line that cannot hear.
        processor_unusable_policy=ProcessorUnusablePolicy.END,
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
