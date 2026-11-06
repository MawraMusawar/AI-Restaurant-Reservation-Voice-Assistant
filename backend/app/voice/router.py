"""WebRTC signaling and voice session endpoints.

The browser's `@pipecat-ai/small-webrtc-transport` sends:
  POST  /api/offer   {sdp, type, pc_id, restart_pc, requestData?}  -> SDP answer
  PATCH /api/offer   {pc_id, candidates: [{candidate, sdp_mid, sdp_mline_index}]}
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from pipecat.transports.smallwebrtc.request_handler import (
    IceCandidate,
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.voice.sessions import SessionLimitError, SessionStatus, VoiceSessionManager

router = APIRouter(tags=["voice"])


@dataclass
class VoiceServices:
    webrtc: SmallWebRTCRequestHandler
    sessions: VoiceSessionManager


def get_voice_services(request: Request) -> VoiceServices:
    return request.app.state.voice


VoiceDep = Annotated[VoiceServices, Depends(get_voice_services)]

BUSY_DETAIL = "All voice lines are busy right now. Please try again in a moment."


# --- Request / response models -------------------------------------------------


class OfferRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    sdp: str = Field(min_length=1, max_length=200_000)
    type: Literal["offer"]
    pc_id: str | None = Field(default=None, max_length=128)
    restart_pc: bool | None = None
    request_data: dict[str, Any] | None = Field(default=None, alias="requestData")

    @field_validator("sdp")
    @classmethod
    def sdp_must_offer_audio(cls, sdp: str) -> str:
        # aiortc silently accepts malformed SDP and produces an empty answer, which would
        # create a session that can never connect. Reject anything that is not a real
        # SDP offering an audio stream.
        if not sdp.lstrip().startswith("v=0") or "m=audio" not in sdp:
            raise ValueError("SDP must be a valid session description with an audio stream")
        return sdp


class OfferAnswer(BaseModel):
    sdp: str
    type: str
    pc_id: str


class IceCandidateIn(BaseModel):
    candidate: str = Field(max_length=2048)
    sdp_mid: str | None = Field(default=None, max_length=64)
    sdp_mline_index: int | None = Field(default=None, ge=0)


class IcePatchRequest(BaseModel):
    pc_id: str = Field(min_length=1, max_length=128)
    candidates: list[IceCandidateIn] = Field(max_length=100)


class VoiceSessionOut(BaseModel):
    session_id: str
    status: SessionStatus
    started_at: datetime
    connected_at: datetime | None
    ended_at: datetime | None
    duration_seconds: float | None
    error: str | None


# --- Endpoints ----------------------------------------------------------------


@router.post("/offer", response_model=OfferAnswer)
async def offer(body: OfferRequest, voice: VoiceDep) -> dict[str, str]:
    is_new_session = body.pc_id is None or not voice.sessions.is_active(body.pc_id)
    if is_new_session and not voice.sessions.has_capacity():
        logger.warning("Rejected voice session: concurrency limit reached")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, BUSY_DETAIL)

    start_error: list[Exception] = []

    async def on_new_connection(connection: Any) -> None:
        # Must not block: the request handler awaits this before returning the answer.
        try:
            voice.sessions.start(connection)
        except SessionLimitError as exc:
            start_error.append(exc)
            await connection.disconnect()

    try:
        answer = await voice.webrtc.handle_web_request(
            request=SmallWebRTCRequest(
                sdp=body.sdp,
                type=body.type,
                pc_id=body.pc_id,
                restart_pc=body.restart_pc,
                request_data=body.request_data,
            ),
            webrtc_connection_callback=on_new_connection,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("WebRTC offer failed: {}: {}", type(exc).__name__, exc)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Could not establish the voice connection."
        ) from exc

    if start_error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, BUSY_DETAIL)
    if answer is None:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "No WebRTC answer was produced.")
    return answer


@router.patch("/offer")
async def ice_candidates(body: IcePatchRequest, voice: VoiceDep) -> dict[str, str]:
    await voice.webrtc.handle_patch_request(
        SmallWebRTCPatchRequest(
            pc_id=body.pc_id,
            candidates=[
                IceCandidate(
                    candidate=c.candidate,
                    sdp_mid=c.sdp_mid,  # type: ignore[arg-type]  # aiortc accepts None
                    sdp_mline_index=c.sdp_mline_index,  # type: ignore[arg-type]
                )
                for c in body.candidates
            ],
        )
    )
    return {"status": "success"}


@router.get("/voice/sessions", response_model=list[VoiceSessionOut])
async def list_voice_sessions(voice: VoiceDep) -> list[VoiceSessionOut]:
    return [
        VoiceSessionOut(
            session_id=s.session_id,
            status=s.status,
            started_at=s.started_at,
            connected_at=s.connected_at,
            ended_at=s.ended_at,
            duration_seconds=s.duration_seconds,
            error=s.error,
        )
        for s in voice.sessions.list_sessions()
    ]
