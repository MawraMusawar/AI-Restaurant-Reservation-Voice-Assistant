"""Real Groq + real tools + real database: book a table, then cancel it.

Runs in a throwaway restaurant (see test_db_reservations.py), so the demo data is
untouched. Spends about 10 Groq requests.
"""

from datetime import timedelta

import pytest

from app.config import get_settings
from app.db import Database
from app.db.reservations import PostgresReservationBackend
from app.db.restaurants import load_profile
from tests.agent_harness import TextConversation

settings = get_settings()
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        settings.database_url is None or settings.groq_api_key is None,
        reason="DATABASE_URL and GROQ_API_KEY required",
    ),
]


async def _status(db: Database, reference: str) -> str | None:
    async with db.pool.acquire() as conn:
        return await conn.fetchval(
            "select status from reservations where booking_reference = $1", reference
        )


async def test_assistant_books_and_cancels_in_the_database(db: Database, restaurant: str) -> None:
    async with db.pool.acquire() as conn:
        profile = await load_profile(conn, restaurant)
    backend = PostgresReservationBackend(db.pool, profile, session_id="llm-db-test")
    now = profile.now()
    day = (now + timedelta(days=3)).date()
    when = f"{day.strftime('%A')} the {day.day}"

    async with TextConversation(settings, backend, now, profile=profile) as conv:
        reply = await conv.say(f"Hi, a table for two on {when} at 7 PM, please.")
        checked = [t.result for t in reply.tool_calls if t.name == "check_availability"]
        assert checked and checked[-1]["available"] is True, reply

        reply = await conv.say("The name is Hina Khan.")
        assert all(t.result.get("ok") is not True for t in reply.tool_calls
                   if t.name == "create_reservation"), "booked before the read-back"  # fmt: skip

        reply = await conv.say("Yes, that's correct.")
        booked = [t.result for t in reply.tool_calls
                  if t.name == "create_reservation" and t.result.get("ok")]  # fmt: skip
        assert booked, reply
        reference = booked[-1]["booking_reference"]
        assert await _status(db, reference) == "confirmed"
        spoken = reply.text.replace("‑", "-").replace(" ", "").replace(",", "").upper()
        assert reference.replace("-", "") in spoken.replace("-", "").replace("DASH", ""), (
            reference,
            reply.text,
        )

        # Change of plans: cancel it, with the booking read back first.
        reply = await conv.say(f"Actually, please cancel booking {reference}.")
        assert await _status(db, reference) == "confirmed", "cancelled without confirmation"
        reply = await conv.say("Yes, please cancel it.")
        assert await _status(db, reference) == "cancelled", reply
