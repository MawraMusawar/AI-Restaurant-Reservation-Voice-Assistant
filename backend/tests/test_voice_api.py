"""Signaling API tests, including a real end-to-end WebRTC audio round trip.

The end-to-end test uses aiortc (installed with pipecat-ai[webrtc]) as a
stand-in for the browser: it sends a 440 Hz tone and checks that the
pipeline's audio loopback sends the tone back over WebRTC.
"""

import asyncio
import fractions
import time
from collections.abc import AsyncIterator

import av
import httpx
import numpy as np
import pytest
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription

from app.main import create_app

SAMPLE_RATE = 48000
SAMPLES_PER_FRAME = 960  # 20 ms


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            c.app = app  # type: ignore[attr-defined]
            yield c


# --- Validation ----------------------------------------------------------------


async def test_offer_rejects_missing_sdp(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/offer", json={"type": "offer"})
    assert response.status_code == 422


async def test_offer_rejects_non_offer_type(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/offer", json={"sdp": "v=0", "type": "answer"})
    assert response.status_code == 422


async def test_offer_with_malformed_sdp_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/offer", json={"sdp": "not an sdp", "type": "offer"})
    assert response.status_code == 422
    # No session must be created for a junk offer.
    assert (await client.get("/api/voice/sessions")).json() == []


async def test_offer_without_audio_stream_is_rejected(client: httpx.AsyncClient) -> None:
    sdp = "v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\ns=-\r\nt=0 0\r\n"
    response = await client.post("/api/offer", json={"sdp": sdp, "type": "offer"})
    assert response.status_code == 422


async def test_offer_returns_503_when_all_lines_busy(client: httpx.AsyncClient) -> None:
    sessions = client.app.state.voice.sessions  # type: ignore[attr-defined]
    sessions._max_concurrent = 0
    sdp = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
    response = await client.post("/api/offer", json={"sdp": sdp, "type": "offer"})
    assert response.status_code == 503


async def test_ice_patch_for_unknown_connection_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.patch(
        "/api/offer",
        json={"pc_id": "missing", "candidates": [{"candidate": "", "sdp_mid": "0"}]},
    )
    assert response.status_code == 404


async def test_sessions_endpoint_starts_empty(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/voice/sessions")
    assert response.status_code == 200
    assert response.json() == []


# --- End-to-end WebRTC audio round trip ------------------------------------------


class ToneTrack(MediaStreamTrack):
    """A real-time paced 440 Hz sine wave, like a microphone that hums."""

    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self._pts = 0
        self._start: float | None = None

    async def recv(self) -> av.AudioFrame:
        if self._start is None:
            self._start = time.monotonic()
        delay = self._start + self._pts / SAMPLE_RATE - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        t = (np.arange(SAMPLES_PER_FRAME) + self._pts) / SAMPLE_RATE
        samples = (0.3 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = SAMPLE_RATE
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, SAMPLE_RATE)
        self._pts += SAMPLES_PER_FRAME
        return frame


@pytest.mark.integration
async def test_webrtc_audio_round_trip(client: httpx.AsyncClient) -> None:
    pc = RTCPeerConnection()
    pc.addTrack(ToneTrack())
    echo_heard = asyncio.Event()
    peak_rms: list[float] = []

    async def listen(track: MediaStreamTrack) -> None:
        while not echo_heard.is_set():
            frame = await track.recv()
            pcm = frame.to_ndarray().astype(np.float64)
            rms = float(np.sqrt(np.mean(pcm**2)))
            peak_rms.append(rms)
            if rms > 1000:  # clearly non-silent: our tone came back
                echo_heard.set()

    listeners: list[asyncio.Task[None]] = []

    @pc.on("track")
    def on_track(track: MediaStreamTrack) -> None:
        if track.kind == "audio":
            listeners.append(asyncio.create_task(listen(track)))

    try:
        await pc.setLocalDescription(await pc.createOffer())
        response = await client.post(
            "/api/offer", json={"sdp": pc.localDescription.sdp, "type": "offer"}
        )
        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer["type"] == "answer" and answer["pc_id"]
        await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type="answer"))

        await asyncio.wait_for(echo_heard.wait(), timeout=20)

        sessions = (await client.get("/api/voice/sessions")).json()
        assert sessions[0]["session_id"] == answer["pc_id"]
        assert sessions[0]["status"] == "active"
    finally:
        for task in listeners:
            task.cancel()
        await pc.close()

    # Closing the peer connection must end the server-side session.
    for _ in range(100):
        sessions = (await client.get("/api/voice/sessions")).json()
        if sessions and sessions[0]["status"] == "ended":
            break
        await asyncio.sleep(0.1)
    assert sessions[0]["status"] == "ended", sessions
    assert max(peak_rms) > 1000
