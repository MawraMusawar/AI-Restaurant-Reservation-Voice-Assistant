"""Persist finished voice sessions (call logs) and their transcripts."""

from dataclasses import asdict

import asyncpg
from loguru import logger

from app.voice.sessions import VoiceSession


def session_metrics(session: VoiceSession) -> dict:
    return {
        "startup_ms": session.startup_ms,
        "duration_seconds": session.duration_seconds,
        "stt_latencies_ms": session.stt_latencies_ms,
        "turn_latencies_ms": session.turn_latencies_ms,
        "response_latencies_ms": session.response_latencies_ms,
        "voice_latencies_ms": session.voice_latencies_ms,
        "dropped_audio_secs": session.dropped_audio_secs,
        "max_loop_lag_ms": session.max_loop_lag_ms,
        "loop_stalls": session.loop_stalls,
        "tool_calls": [{**asdict(c), "at": c.at.isoformat()} for c in session.tool_calls],
    }


async def save_session(
    pool: asyncpg.Pool, session: VoiceSession, restaurant_id: str | None
) -> None:
    """Upsert the session row and replace its messages, in one transaction."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            insert into voice_sessions (
                id, restaurant_id, status, end_reason, started_at, connected_at, ready_at,
                ended_at, error, errors, metrics
            ) values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            on conflict (id) do update set
                status = excluded.status, end_reason = excluded.end_reason,
                connected_at = excluded.connected_at, ready_at = excluded.ready_at,
                ended_at = excluded.ended_at, error = excluded.error,
                errors = excluded.errors, metrics = excluded.metrics
            """,
            session.session_id,
            restaurant_id,
            session.status,
            session.end_reason,
            session.started_at,
            session.connected_at,
            session.ready_at,
            session.ended_at,
            session.error,
            session.errors,
            session_metrics(session),
        )
        await conn.execute(
            "delete from conversation_messages where session_id = $1", session.session_id
        )
        await conn.executemany(
            """
            insert into conversation_messages
                (session_id, position, role, content, interrupted, spoken_at)
            values ($1, $2, $3, $4, $5, $6)
            """,
            [
                (session.session_id, i, t.role, t.text, t.interrupted, t.at)
                for i, t in enumerate(session.transcript)
            ],
        )
    logger.info(
        "Call log saved | session_id={} messages={}", session.session_id, len(session.transcript)
    )
