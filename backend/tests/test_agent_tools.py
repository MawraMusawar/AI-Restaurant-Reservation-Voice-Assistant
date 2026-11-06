"""Reservation tool handlers and prompt: validation, guards, structured results."""

from datetime import datetime
from types import SimpleNamespace
from typing import Any

from app.agent.booking_rules import BookingSlot
from app.agent.prompt import build_greeting, build_system_prompt
from app.agent.reservations import NewReservation, OfflineReservationBackend
from app.agent.restaurant import DEMO_RESTAURANT, ReservationRules
from app.agent.tools import ReservationTools

NOW = datetime(2026, 11, 6, 21, 0, tzinfo=DEMO_RESTAURANT.tz)  # Friday evening
SLOT = {"date": "2026-11-07", "time": "19:00", "guests": 4}


class RecordingBackend:
    """Test double: records what reached the backend and answers 'ok'."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def check_availability(self, slot: BookingSlot) -> dict[str, Any]:
        self.calls.append(("check_availability", slot))
        return {"ok": True, "available": True}

    async def get_available_tables(self, slot: BookingSlot) -> dict[str, Any]:
        self.calls.append(("get_available_tables", slot))
        return {"ok": True, "tables": []}

    async def create_reservation(self, reservation: NewReservation) -> dict[str, Any]:
        self.calls.append(("create_reservation", reservation))
        return {"ok": True, "booking_reference": "OE-TEST01"}

    async def get_reservation(self, reference: str | None, name: str | None, phone: str | None):
        self.calls.append(("get_reservation", (reference, name, phone)))
        return {"ok": True, "reservations": []}

    async def cancel_reservation(self, reference: str) -> dict[str, Any]:
        self.calls.append(("cancel_reservation", reference))
        return {"ok": True, "status": "cancelled"}


def make_tools(backend: Any = None, profile=DEMO_RESTAURANT, observer=None) -> ReservationTools:
    return ReservationTools(profile, backend or RecordingBackend(), observer, clock=lambda: NOW)


BOOKING = {**SLOT, "customer_name": "Sara"}


async def test_first_create_call_only_stages_details_for_read_back() -> None:
    backend = RecordingBackend()
    tools = make_tools(backend)
    # Even when the model claims the caller confirmed, the first call never books.
    result = await tools.create_reservation({**BOOKING, "caller_confirmed": True}, turn=3)
    assert result["ok"] is False
    assert result["error_code"] == "needs_confirmation"
    assert result["details_to_confirm"]["customer_name"] == "Sara"
    assert result["details_to_confirm"]["weekday"] == "Saturday"
    assert backend.calls == []


async def test_create_books_only_after_caller_confirms_in_a_later_turn() -> None:
    backend = RecordingBackend()
    tools = make_tools(backend)
    await tools.create_reservation(BOOKING, turn=3)  # staged, read back
    # Same turn again: the caller hasn't answered the read-back yet.
    same_turn = await tools.create_reservation({**BOOKING, "caller_confirmed": True}, turn=3)
    assert same_turn["error_code"] == "needs_confirmation"
    # Next turn, but not confirmed (e.g. the caller hesitated).
    unconfirmed = await tools.create_reservation(BOOKING, turn=4)
    assert unconfirmed["error_code"] == "needs_confirmation"
    assert backend.calls == []
    booked = await tools.create_reservation({**BOOKING, "caller_confirmed": True}, turn=4)
    assert booked == {"ok": True, "booking_reference": "OE-TEST01"}
    assert [name for name, _ in backend.calls] == ["create_reservation"]


async def test_changed_details_must_be_read_back_again() -> None:
    backend = RecordingBackend()
    tools = make_tools(backend)
    await tools.create_reservation(BOOKING, turn=3)
    changed = {**BOOKING, "guests": 6, "caller_confirmed": True}  # caller: "make it six"
    assert (await tools.create_reservation(changed, turn=4))["error_code"] == "needs_confirmation"
    assert backend.calls == []
    assert (await tools.create_reservation(changed, turn=5))["ok"] is True
    assert backend.calls[0][1].slot.guests == 6


async def test_create_validates_details_before_touching_the_backend() -> None:
    backend = RecordingBackend()
    tools = make_tools(backend)
    base = {**SLOT, "caller_confirmed": True}
    assert (await tools.create_reservation({**base, "customer_name": "  "}, turn=1))[
        "error_code"
    ] == "missing_name"
    bad_phone = {**base, "customer_name": "Ali", "phone": "12"}
    assert (await tools.create_reservation(bad_phone))["error_code"] == "invalid_phone"
    monday = {**base, "customer_name": "Ali", "date": "2026-11-09"}
    assert (await tools.create_reservation(monday))["error_code"] == "closed_on_date"
    assert backend.calls == []


async def test_create_passes_cleaned_details_to_backend() -> None:
    backend = RecordingBackend()
    tools = make_tools(backend)
    args = {
        "customer_name": "  Sara   Khan ",
        "phone": "+92 300 123-4567",
        "date": "2026-11-07",
        "time": "7:30 PM",
        "guests": "4",  # models sometimes send numbers as strings
        "special_requests": "window   seat",
        "caller_confirmed": True,
    }
    await tools.create_reservation(args, turn=1)
    result = await tools.create_reservation(args, turn=2)
    assert result == {"ok": True, "booking_reference": "OE-TEST01"}
    [(name, reservation)] = backend.calls
    assert name == "create_reservation"
    assert reservation.customer_name == "Sara Khan"
    assert reservation.phone == "+923001234567"
    assert reservation.special_requests == "window seat"
    assert reservation.slot.guests == 4 and reservation.slot.time.hour == 19


async def test_phone_can_be_required_by_the_restaurant() -> None:
    profile = DEMO_RESTAURANT.model_copy(update={"rules": ReservationRules(require_phone=True)})
    result = await make_tools(profile=profile).create_reservation(
        {**SLOT, "customer_name": "Ali", "caller_confirmed": True}
    )
    assert result["error_code"] == "missing_phone"
    assert "and a phone number" in build_system_prompt(profile, NOW)


async def test_lookup_needs_reference_or_name_and_phone() -> None:
    tools = make_tools()
    assert (await tools.get_reservation({"customer_name": "Ali"}))[
        "error_code"
    ] == "missing_lookup_details"
    assert (await tools.get_reservation({"booking_reference": "??"}))[
        "error_code"
    ] == "invalid_reference"
    assert (await tools.get_reservation({"booking_reference": "oe-7k3p"}))["ok"] is True


async def test_cancel_reads_the_booking_back_before_cancelling() -> None:
    backend = RecordingBackend()
    tools = make_tools(backend)
    args = {"booking_reference": "oe-7k3p", "caller_confirmed": True}
    staged = await tools.cancel_reservation(args, turn=2)
    assert staged["error_code"] == "needs_confirmation"
    assert staged["booking"]["ok"] is True
    assert backend.calls == [("get_reservation", ("OE-7K3P", None, None))]
    assert (await tools.cancel_reservation(args, turn=3))["ok"] is True
    assert backend.calls[-1] == ("cancel_reservation", "OE-7K3P")


async def test_offline_backend_never_reports_availability_or_bookings() -> None:
    tools = make_tools(OfflineReservationBackend())
    availability = await tools.check_availability(SLOT)
    assert availability["ok"] is False
    assert availability["error_code"] == "booking_system_unavailable"
    assert availability["requested"]["weekday"] == "Saturday"
    args = {**SLOT, "customer_name": "Ali", "caller_confirmed": True}
    await tools.create_reservation(args, turn=1)
    booking = await tools.create_reservation(args, turn=2)
    assert booking["error_code"] == "booking_system_unavailable"
    assert "booking_reference" not in booking
    cancel = await tools.cancel_reservation({"booking_reference": "OE-7K3P"}, turn=2)
    assert cancel["error_code"] == "booking_system_unavailable"


async def test_restaurant_info_comes_from_the_profile() -> None:
    info = await make_tools().get_restaurant_info({})
    assert info["name"] == DEMO_RESTAURANT.name
    assert "Monday: closed" in info["opening_hours"]


async def test_handler_errors_become_structured_results() -> None:
    class BrokenBackend(RecordingBackend):
        async def check_availability(self, slot: BookingSlot) -> dict[str, Any]:
            raise ConnectionError("database down")

    observed: list[tuple[str, dict, float]] = []
    tools = make_tools(BrokenBackend(), observer=lambda *args: observed.append(args))
    handler = next(t for t in tools.schema().standard_tools if t.name == "check_availability")
    results: list[dict] = []

    async def result_callback(result: dict, **_kwargs) -> None:
        results.append(result)

    params = SimpleNamespace(
        function_name="check_availability",
        arguments=SLOT,
        result_callback=result_callback,
        context=None,
    )
    await handler.handler(params)
    assert results[0]["error_code"] == "internal_error"
    assert observed[0][0] == "check_availability" and observed[0][1]["ok"] is False


def test_schema_exposes_all_six_tools() -> None:
    names = [t.name for t in make_tools().schema().standard_tools]
    assert names == [
        "get_restaurant_info",
        "check_availability",
        "get_available_tables",
        "create_reservation",
        "get_reservation",
        "cancel_reservation",
    ]


def test_prompt_anchors_dates_in_restaurant_time() -> None:
    prompt = build_system_prompt(DEMO_RESTAURANT, NOW)
    assert "Friday 2026-11-06, 21:00" in prompt
    assert "Sat 2026-11-07" in prompt and "Fri 2026-11-13" in prompt
    assert "Monday: closed" in prompt
    assert DEMO_RESTAURANT.name in build_greeting(DEMO_RESTAURANT)


async def test_handler_counts_caller_turns_from_the_context() -> None:
    backend = RecordingBackend()
    tools = make_tools(backend)
    create = next(t for t in tools.schema().standard_tools if t.name == "create_reservation")
    results: list[dict] = []

    async def result_callback(result: dict, **_kwargs) -> None:
        results.append(result)

    def call(user_turns: int) -> SimpleNamespace:
        messages = [{"role": "user", "content": "..."}] * user_turns
        return SimpleNamespace(
            function_name="create_reservation",
            arguments={**BOOKING, "caller_confirmed": True},
            result_callback=result_callback,
            context=SimpleNamespace(get_messages=lambda: messages),
        )

    await create.handler(call(3))
    await create.handler(call(4))
    assert [r["ok"] for r in results] == [False, True]
