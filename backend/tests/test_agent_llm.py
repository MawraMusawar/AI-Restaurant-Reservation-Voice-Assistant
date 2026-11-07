"""Real Groq conversations: the agent's decisions, checked through its tool calls.

These spend Groq free-tier quota (about 30K tokens for the whole file, out of
200K per model per day), so they are marked `integration` and kept few. The
assertions check structured tool calls and arguments, which are what matter
for correctness, and only loosely check the wording, which varies.

The scripted backend stands in for the Milestone 6 database so the whole
booking flow can run. The production pipeline uses `OfflineReservationBackend`
until then, and the last test checks the agent stays honest with it.
"""

from datetime import datetime
from typing import Any

import pytest

from app.agent.booking_rules import BookingSlot
from app.agent.reservations import NewReservation, OfflineReservationBackend
from app.agent.restaurant import DEMO_RESTAURANT
from app.config import get_settings
from app.db.reservations import DATABASE_ERROR
from tests.agent_harness import TextConversation

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(get_settings().groq_api_key is None, reason="GROQ_API_KEY not set"),
]

# Friday 6 November 2026, 3 PM in Lahore. "Tomorrow" = Saturday 2026-11-07.
NOW = datetime(2026, 11, 6, 15, 0, tzinfo=DEMO_RESTAURANT.tz)


class ScriptedBackend:
    """7 PM is always taken (alternatives 6:30 and 8:15); everything else is free."""

    def __init__(self) -> None:
        self.created: list[NewReservation] = []

    async def check_availability(self, slot: BookingSlot) -> dict[str, Any]:
        if (slot.time.hour, slot.time.minute) == (19, 0):
            day = slot.date.isoformat()
            return {
                "ok": True,
                "available": False,
                "alternatives": [{"date": day, "time": "18:30"}, {"date": day, "time": "20:15"}],
            }
        return {"ok": True, "available": True}

    async def get_available_tables(self, slot: BookingSlot) -> dict[str, Any]:
        return {"ok": True, "tables": [{"table": "T4", "seats": 6, "area": "terrace"}]}

    async def create_reservation(self, reservation: NewReservation) -> dict[str, Any]:
        self.created.append(reservation)
        return {"ok": True, "booking_reference": "OE-4821", "status": "confirmed"}

    async def get_reservation(self, *_args: Any) -> dict[str, Any]:
        return {"ok": False, "error_code": "not_found", "message": "No booking found."}

    async def cancel_reservation(self, _reference: str) -> dict[str, Any]:
        return {"ok": False, "error_code": "not_found", "message": "No booking found."}


def _names(reply) -> list[str]:
    return [t.name for t in reply.tool_calls]


def _checked(reply) -> list[dict]:
    return [t.result["requested"] for t in reply.tool_calls if t.name == "check_availability"]


async def test_full_booking_flow_with_alternative_and_confirmation() -> None:
    backend = ScriptedBackend()
    async with TextConversation(get_settings(), backend, NOW) as conv:
        # Missing information: the agent asks instead of guessing or calling tools.
        reply = await conv.say("Hi, I'd like to book a table.")
        assert _names(reply) == [] and "?" in reply.text, reply

        # Relative date resolved to Saturday; 7 PM is taken, so alternatives are offered.
        reply = await conv.say("For four people, tomorrow at 7 in the evening.")
        checked = _checked(reply)
        assert checked and checked[0]["date"] == "2026-11-07", reply
        assert checked[0]["time"] == "19:00" and checked[0]["guests"] == 4, reply
        assert any(t in reply.text for t in ("6:30", "8:15")), reply

        reply = await conv.say("8:15 works for us.")
        assert "create_reservation" not in _names(reply) or not backend.created, reply

        # Name given: the details are read back, nothing is booked yet.
        reply = await conv.say("It's under Ali Raza.")
        assert backend.created == [], reply
        assert "Ali" in reply.text and "8:15" in reply.text, reply

        reply = await conv.say("Yes, that's right.")
        assert len(backend.created) == 1, reply
        booking = backend.created[0]
        assert booking.customer_name == "Ali Raza"
        assert (booking.slot.date.isoformat(), booking.slot.time.strftime("%H:%M")) == (
            "2026-11-07",
            "20:15",
        )
        assert booking.slot.guests == 4
        assert "OE-4821" in reply.text.replace("‑", "-"), reply


async def test_correction_replaces_old_value_and_rechecks() -> None:
    backend = ScriptedBackend()
    async with TextConversation(get_settings(), backend, NOW) as conv:
        reply = await conv.say("Table for two on Sunday at 8 PM please.")
        checked = _checked(reply)
        assert checked and checked[-1]["guests"] == 2 and checked[-1]["date"] == "2026-11-08"

        reply = await conv.say("Sorry, actually make that six people.")
        checked = _checked(reply)
        assert checked, f"availability was not re-checked after the correction: {reply}"
        assert checked[-1]["guests"] == 6 and checked[-1]["time"] == "20:00", reply
        assert backend.created == []


async def test_hours_closed_day_and_honesty_while_booking_system_is_offline() -> None:
    async with TextConversation(get_settings(), OfflineReservationBackend(), NOW) as conv:
        reply = await conv.say("What time do you close on Sunday?")
        assert "9:30" in reply.text or "21:30" in reply.text, reply
        # Hours are in the prompt: answered without a tool call (one request, not two).
        assert _names(reply) == [] and reply.llm_requests == 1, reply

        # Closed on Mondays: the agent must say so, not offer a Monday table.
        reply = await conv.say("Can I book Monday at 7 for two people?")
        assert "closed" in reply.text.lower(), reply

        # The booking system is offline (no database yet): never claim availability.
        reply = await conv.say("Okay, Tuesday at 7 then.")
        assert "check_availability" in _names(reply), reply
        text = reply.text.lower()
        assert "booked" not in text.replace("not booked", "") and "is available" not in text, reply
        assert "create_reservation" not in _names(reply)


async def test_vague_time_gets_a_clarifying_question() -> None:
    backend = ScriptedBackend()
    async with TextConversation(get_settings(), backend, NOW) as conv:
        reply = await conv.say("Can I get a table for four on Saturday, maybe 7 or 8?")
        assert "?" in reply.text, reply
        assert backend.created == [] and "create_reservation" not in _names(reply), reply


async def test_goodbye_ends_the_call() -> None:
    async with TextConversation(get_settings(), ScriptedBackend(), NOW) as conv:
        reply = await conv.say("No, that's everything. Thanks, bye!")
        assert conv.ended, f"the assistant did not hang up: {reply}"
        # If the model hangs up without words, the pipeline speaks FALLBACK_GOODBYE
        # (tested in test_call_control.py), so the line never just goes dead.


class FailingBackend(ScriptedBackend):
    """The database is down: every operation fails like PostgresReservationBackend does."""

    async def check_availability(self, slot: BookingSlot) -> dict[str, Any]:
        return dict(DATABASE_ERROR)

    async def create_reservation(self, reservation: NewReservation) -> dict[str, Any]:
        return await self.check_availability(reservation.slot)  # type: ignore[return-value]


async def test_database_failure_is_explained_never_booked() -> None:
    backend = FailingBackend()
    async with TextConversation(get_settings(), backend, NOW) as conv:
        reply = await conv.say("A table for two tomorrow at 8 PM, please.")
        text = reply.text.lower()
        assert "check_availability" in _names(reply), reply
        assert any(w in text for w in ("sorry", "apolog", "problem", "trouble", "unable")), reply
        assert "booked" not in text.replace("not booked", "").replace("nothing was booked", "")
        assert backend.created == []


async def test_invalid_requests_are_explained() -> None:
    async with TextConversation(get_settings(), ScriptedBackend(), NOW) as conv:
        big = await conv.say("I need a table for 14 people on Saturday at 8 PM.")
        assert any(w in big.text.lower() for w in ("10", "ten", "email")), big
        past = await conv.say("Actually, make it 2 people, yesterday at 8 PM.")
        assert any(w in past.text.lower() for w in ("passed", "past", "already", "yesterday")), past


async def test_long_call_stays_responsive() -> None:
    """A long, meandering call: replies keep coming, context stays within the budget."""
    turns = [
        "Hi there.",
        "Where are you located?",
        "Do you have vegetarian options?",
        "What time do you close on Friday?",
        "Okay, I'd like a table for three on Friday at 8 PM.",
        "Actually, make that four people.",
        "The name is Omar Farooq.",
        "Yes, that's right.",
        "Is parking available?",
        "Can I bring a child? Do you have high chairs?",
        "Great, thanks. That's all, goodbye!",
    ]
    backend = ScriptedBackend()
    latencies = []
    async with TextConversation(get_settings(), backend, NOW) as conv:
        for line in turns:
            reply = await conv.say(line)
            assert reply.text.strip() or conv.ended, f"no reply to {line!r}"
            latencies.append(reply.latency_ms)
        context_chars = sum(len(str(m)) for m in conv.context.get_messages())
    print(f"\n[long call] reply ms={latencies} context_chars={context_chars} "
          f"failovers={len(conv.failovers)}")  # fmt: skip
    assert len(backend.created) == 1 and backend.created[0].slot.guests == 4
    assert conv.ended, "the assistant should hang up after the goodbye"
    # ~11 turns with tool results: still far below a request size that would strain the
    # free tier (system prompt + tools are ~5K chars on top).
    assert context_chars < 20_000, context_chars
