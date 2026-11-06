"""The Pipecat voice pipeline for one call.

Milestone 1 pipeline (audio echo, no API keys needed):

    browser mic -> SmallWebRTC input -> Silero VAD -> speaking events
                -> audio loopback -> SmallWebRTC output -> browser speaker

Milestones 2-5 replace the loopback with Deepgram STT -> Groq LLM -> ElevenLabs TTS.
"""

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

from app.voice.processors import AudioLoopback, VADSpeakingBridge
from app.voice.sessions import VoiceSession

# End the call if nothing happens (no speech, no bot audio) for this long.
IDLE_TIMEOUT_SECS = 300


async def run_voice_bot(connection: SmallWebRTCConnection, session: VoiceSession) -> None:
    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_10ms_chunks=2,
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            VADProcessor(vad_analyzer=SileroVADAnalyzer()),
            VADSpeakingBridge(),
            AudioLoopback(),
            transport.output(),
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True),
        idle_timeout_secs=IDLE_TIMEOUT_SECS,
        conversation_id=session.session_id,
    )
    runner = WorkerRunner(handle_sigint=False)

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
