"""Reservation engine against the real database (DATABASE_URL).

Each test gets its own throwaway restaurant (the `restaurant` fixture in
conftest.py), so the demo data is never touched.
"""

import asyncio
import secrets
from datetime import UTC, datetime, time, timedelta

import asyncpg
import pytest

from app.agent.booking_rules import BookingSlot
from app.agent.reservations import NewReservation
from app.config import get_settings
from app.db import Database, create_database
from app.db.reservations import PostgresReservationBackend
from app.db.restaurants import load_profile
from app.db.sessions import save_session
from app.voice.sessions import VoiceSession

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(get_settings().database_url is None, reason="DATABASE_URL not set"),
]


async def backend_for(db: Database, slug: str) -> PostgresReservationBackend:
    async with db.pool.acquire() as conn:
        profile = await load_profile(conn, slug)
    return PostgresReservationBackend(db.pool, profile, session_id="test-session")


def slot(guests: int, at: time = time(19, 0), days_ahead: int = 3) -> BookingSlot:
    day = (datetime.now(UTC) + timedelta(days=days_ahead)).date()
    return BookingSlot(date=day, time=at, guests=guests)


def booking(s: BookingSlot, name: str = "Ali Raza", phone: str | None = None) -> NewReservation:
    return NewReservation(slot=s, customer_name=name, phone=phone, special_requests=None)


async def test_profile_loads_hours_rules_and_assistant_defaults(db: Database, restaurant: str):
    async with db.pool.acquire() as conn:
        profile = await load_profile(conn, restaurant)
    assert profile.name == "Test Bistro" and profile.booking_reference_prefix == "TS"
    assert all(h is not None and h.opens == time(12, 0) for h in profile.hours)
    assert profile.rules.dining_duration_minutes == 90 and profile.assistant_name == "Sara"


async def test_booking_is_stored_and_found_by_reference(db: Database, restaurant: str):
    backend = await backend_for(db, restaurant)
    s = slot(2)
    assert (await backend.check_availability(s))["available"] is True

    result = await backend.create_reservation(booking(s, phone="+923001234567"))
    assert result["ok"] is True and result["status"] == "confirmed"
    reference = result["booking_reference"]
    assert reference.startswith("TS-") and len(reference) == 9
    assert (result["time"], result["guests"], result["seating_area"]) == ("19:00", 2, "window")

    found = await backend.get_reservation(reference, None, None)
    assert found["ok"] and found["reservations"][0]["customer_name"] == "Ali Raza"
    by_phone = await backend.get_reservation(None, "Ali", "+923001234567")
    assert by_phone["reservations"][0]["booking_reference"] == reference

    async with db.pool.acquire() as conn:
        row = await conn.fetchrow(
            "select status, voice_session_id, starts_at from reservations "
            "where booking_reference = $1",
            reference,
        )
    assert row["status"] == "confirmed" and row["voice_session_id"] == "test-session"
    # Stored as an absolute instant: 19:00 in Lahore is 14:00 UTC.
    assert row["starts_at"].astimezone(UTC).time() == time(14, 0)


async def test_tables_fill_best_fit_first_then_alternatives_are_offered(
    db: Database, restaurant: str
):
    backend = await backend_for(db, restaurant)
    s = slot(2)
    first = await backend.create_reservation(booking(s, "Guest One"))
    second = await backend.create_reservation(booking(s, "Guest Two"))
    assert first["seating_area"] == "window"  # T2 (2 seats) first
    assert second["seating_area"] == "main"  # then T4

    availability = await backend.check_availability(s)
    assert availability["available"] is False
    # Both tables are busy 19:00-20:30: 17:30 (ends at 19:00) and 20:30 are equally near.
    times = [a["time"] for a in availability["alternatives"]]
    assert "17:30" in times and "20:30" in times and times == sorted(times)

    third = await backend.create_reservation(booking(s, "Guest Three"))
    assert third["ok"] is False and third["error_code"] == "slot_unavailable"
    assert "booking_reference" not in third and third["alternatives"]


async def test_concurrent_callers_cannot_double_book_the_last_table(db: Database, restaurant: str):
    """Five simultaneous bookings for 4 people: only T4 fits, so exactly one wins."""
    backend = await backend_for(db, restaurant)
    s = slot(4)
    results = await asyncio.gather(
        *(backend.create_reservation(booking(s, f"Caller {i}")) for i in range(5))
    )
    winners = [r for r in results if r["ok"]]
    losers = [r for r in results if not r["ok"]]
    assert len(winners) == 1, results
    assert all(r["error_code"] == "slot_unavailable" for r in losers), losers
    async with db.pool.acquire() as conn:
        count = await conn.fetchval(
            "select count(*) from reservations r join restaurants x on x.id = r.restaurant_id "
            "where x.slug = $1 and r.status = 'confirmed'",
            restaurant,
        )
    assert count == 1


async def test_database_constraint_blocks_overlap_even_without_the_backend(
    db: Database, restaurant: str
):
    """The exclusion constraint holds on its own, e.g. against a buggy admin script."""
    async with db.pool.acquire() as conn:
        rid, table_id = await conn.fetchrow(
            "select r.id, t.id from restaurants r join restaurant_tables t "
            "on t.restaurant_id = r.id where r.slug = $1 and t.label = 'T2'",
            restaurant,
        )
        customer = await conn.fetchval(
            "insert into customers (restaurant_id, name) values ($1, 'X') returning id", rid
        )
        start = datetime.now(UTC) + timedelta(days=2)
        insert = (
            "insert into reservations (restaurant_id, table_id, customer_id, "
            "booking_reference, party_size, starts_at, ends_at) "
            "values ($1, $2, $3, $4, 2, $5, $6)"
        )
        await conn.execute(
            insert, rid, table_id, customer, "TS-AAAAAA", start, start + timedelta(hours=1)
        )
        with pytest.raises(asyncpg.exceptions.ExclusionViolationError):
            await conn.execute(
                insert, rid, table_id, customer, "TS-BBBBBB",
                start + timedelta(minutes=30), start + timedelta(minutes=90),
            )  # fmt: skip
        # Back-to-back is fine: [start, end) ranges don't overlap.
        await conn.execute(
            insert, rid, table_id, customer, "TS-CCCCCC",
            start + timedelta(hours=1), start + timedelta(hours=2),
        )  # fmt: skip


async def test_cancelling_frees_the_table(db: Database, restaurant: str):
    backend = await backend_for(db, restaurant)
    s = slot(3)  # only T4 fits
    reference = (await backend.create_reservation(booking(s)))["booking_reference"]
    assert (await backend.check_availability(s))["available"] is False

    cancelled = await backend.cancel_reservation(reference)
    assert cancelled["ok"] is True and cancelled["status"] == "cancelled"
    assert (await backend.check_availability(s))["available"] is True
    again = await backend.cancel_reservation(reference)
    assert again["ok"] is False and again["error_code"] == "not_active"
    missing = await backend.cancel_reservation("TS-ZZZZZZ")
    assert missing["error_code"] == "not_found"


async def test_party_too_large_for_any_table(db: Database, restaurant: str):
    backend = await backend_for(db, restaurant)
    result = await backend.check_availability(slot(6))
    assert result["available"] is False and result["alternatives"] == []


async def test_database_failure_is_never_reported_as_a_booking(db: Database, restaurant: str):
    backend = await backend_for(db, restaurant)
    broken = await create_database(get_settings())
    await broken.close()  # every acquire() now fails
    backend._pool = broken.pool
    result = await backend.create_reservation(booking(slot(2)))
    assert result["ok"] is False and result["error_code"] == "database_error"
    assert "booking_reference" not in result
    assert (await backend.check_availability(slot(2)))["error_code"] == "database_error"


async def test_call_log_is_saved_with_its_transcript(db: Database, restaurant: str):
    session = VoiceSession(session_id=f"test-call-{secrets.token_hex(4)}")
    session.mark_active()
    session.add_transcript("assistant", "Thank you for calling.")
    session.add_transcript("user", "A table for two, please.")
    session.status, session.end_reason = "ended", "caller_hung_up"
    session.ended_at = datetime.now(UTC)
    try:
        await save_session(db.pool, session, None)
        await save_session(db.pool, session, None)  # idempotent (upsert)
        async with db.pool.acquire() as conn:
            row = await conn.fetchrow(
                "select status, end_reason, metrics from voice_sessions where id = $1",
                session.session_id,
            )
            messages = await conn.fetch(
                "select role, content from conversation_messages "
                "where session_id = $1 order by position",
                session.session_id,
            )
        assert (row["status"], row["end_reason"]) == ("ended", "caller_hung_up")
        assert "voice_latencies_ms" in row["metrics"]
        assert [(m["role"], m["content"]) for m in messages] == [
            ("assistant", "Thank you for calling."),
            ("user", "A table for two, please."),
        ]
    finally:
        async with db.pool.acquire() as conn:
            await conn.execute("delete from voice_sessions where id = $1", session.session_id)
