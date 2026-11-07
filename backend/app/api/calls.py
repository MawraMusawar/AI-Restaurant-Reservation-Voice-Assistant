"""Call logs: finished calls from the database, plus calls in progress (in memory)."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.api.auth import AdminDep
from app.api.deps import ConnDep, ProfileDep
from app.api.schemas import ReservationOut
from app.db.reports import get_call, list_calls
from app.voice.router import VoiceDep
from app.voice.sessions import VoiceSession

router = APIRouter(tags=["calls"])


class CallSummary(BaseModel):
    id: str
    status: Literal["connecting", "active", "ended", "error"]
    end_reason: str | None
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: float | None
    message_count: int
    first_request: str | None
    booking_reference: str | None
    error: str | None
    error_count: int
    live: bool  # still in progress


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    interrupted: bool
    spoken_at: datetime


class CallDetail(BaseModel):
    id: str
    status: str
    end_reason: str | None
    started_at: datetime
    connected_at: datetime | None
    ready_at: datetime | None
    ended_at: datetime | None
    error: str | None
    errors: list[str]
    metrics: dict
    transcript: list[Message]
    reservations: list[ReservationOut]
    live: bool


def _live_summary(s: VoiceSession) -> CallSummary:
    first = next((t.text for t in s.transcript if t.role == "user"), None)
    return CallSummary(
        id=s.session_id,
        status=s.status,
        end_reason=s.end_reason,
        started_at=s.started_at,
        ended_at=s.ended_at,
        duration_seconds=s.duration_seconds,
        message_count=len(s.transcript),
        first_request=first,
        booking_reference=None,
        error=s.error,
        error_count=len(s.errors),
        live=True,
    )


@router.get("/calls", response_model=list[CallSummary])
async def get_calls(
    _admin: AdminDep,
    conn: ConnDep,
    profile: ProfileDep,
    voice: VoiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CallSummary]:
    live = [
        _live_summary(s)
        for s in voice.sessions.list_sessions()
        if voice.sessions.is_active(s.session_id)
    ]
    stored = [
        CallSummary(**r, live=False) for r in await list_calls(conn, profile.id, limit, offset)
    ]
    return (live if offset == 0 else []) + stored


@router.get("/calls/{session_id}", response_model=CallDetail)
async def get_call_detail(
    session_id: str, _admin: AdminDep, conn: ConnDep, profile: ProfileDep, voice: VoiceDep
) -> CallDetail:
    if voice.sessions.is_active(session_id):
        s = next(x for x in voice.sessions.list_sessions() if x.session_id == session_id)
        return CallDetail(
            id=s.session_id,
            status=s.status,
            end_reason=s.end_reason,
            started_at=s.started_at,
            connected_at=s.connected_at,
            ready_at=s.ready_at,
            ended_at=s.ended_at,
            error=s.error,
            errors=list(s.errors),
            metrics={"voice_latencies_ms": s.voice_latencies_ms},
            transcript=[
                Message(role=t.role, content=t.text, interrupted=t.interrupted, spoken_at=t.at)
                for t in s.transcript
            ],
            reservations=[],
            live=True,
        )
    call = await get_call(conn, profile, session_id)
    if call is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such call.")
    return CallDetail(**call, live=False)
