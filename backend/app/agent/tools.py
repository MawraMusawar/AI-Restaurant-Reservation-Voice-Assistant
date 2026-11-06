"""LLM-callable reservation tools (structured function calling, no text parsing).

Each tool validates the model's arguments, applies the restaurant's booking
rules, and only then calls the reservation backend. Results are always a dict
with `ok`; failures carry an `error_code` and a `message` the assistant uses to
explain the problem. A handler never raises into the pipeline: unexpected
errors become `internal_error` results, so the caller hears an apology
instead of silence.

Booking and cancelling are two-step, enforced here rather than trusted to the
prompt (in testing, the model booked without a read-back despite the prompt):
the first call only stages the details and returns them to be read back. The
backend is called only by a later call with the same details, made after the
caller has spoken again, with `caller_confirmed` true.

Descriptions are deliberately short. The tool schemas are sent with every LLM
request and count against Groq's per-minute token budget.
"""

import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from loguru import logger
from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams

from app.agent.booking_rules import BookingSlot, RuleViolation, validate_booking_request
from app.agent.reservations import NewReservation, ReservationBackend
from app.agent.restaurant import RestaurantProfile

# (tool name, result, duration in ms) -> None. Used for call logs and metrics.
ToolObserver = Callable[[str, dict[str, Any], float], None]
Handler = Callable[[dict[str, Any], int], Awaitable[dict[str, Any]]]

MAX_NAME_CHARS = 80
MAX_REQUEST_CHARS = 300
_REFERENCE_RE = re.compile(r"^[A-Z0-9-]{4,20}$")

_DATE = {"type": "string", "description": "YYYY-MM-DD"}
_TIME = {"type": "string", "description": "24-hour HH:MM"}
_GUESTS = {"type": "integer", "minimum": 1, "description": "Party size"}
_CONFIRMED = {
    "type": "boolean",
    "description": "True only after the caller said yes to your read-back of these details",
}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error_code": code, "message": message}


def _as_int(value: Any) -> Any:
    """Models sometimes send numbers as strings ("4")."""
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def _clean_text(value: Any, max_chars: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:max_chars] if text else None


def _clean_phone(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    digits = re.sub(r"\D", "", value)
    if not 7 <= len(digits) <= 15:
        return None
    return ("+" if value.strip().startswith("+") else "") + digits


def _clean_reference(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    reference = re.sub(r"\s+", "", value).upper()
    return reference if _REFERENCE_RE.match(reference) else None


class ReservationTools:
    """The six reservation tools, bound to one restaurant and one backend."""

    def __init__(
        self,
        profile: RestaurantProfile,
        backend: ReservationBackend,
        observer: ToolObserver | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._profile = profile
        self._backend = backend
        self._observer = observer
        self._clock = clock or profile.now
        # tool name -> (staged details, caller turn number when staged)
        self._staged: dict[str, tuple[tuple[Any, ...], int]] = {}

    def schema(self, extra: list[FunctionSchema] | None = None) -> ToolsSchema:
        """The reservation tools, plus any `extra` ones (e.g. call control)."""
        return ToolsSchema(standard_tools=[*self.functions(), *(extra or [])])

    def functions(self) -> list[FunctionSchema]:
        slot = {"date": _DATE, "time": _TIME, "guests": _GUESTS}
        return [
            FunctionSchema(
                name="get_restaurant_info",
                description="Address, opening hours, contact details, services and policies.",
                properties={},
                required=[],
                handler=self._wrap(self.get_restaurant_info),
            ),
            FunctionSchema(
                name="check_availability",
                description="Check if a table is free. Returns alternative times if not.",
                properties=slot,
                required=["date", "time", "guests"],
                handler=self._wrap(self.check_availability),
            ),
            FunctionSchema(
                name="get_available_tables",
                description="List tables (seats, area) that can seat the party at that time.",
                properties=slot,
                required=["date", "time", "guests"],
                handler=self._wrap(self.get_available_tables),
            ),
            FunctionSchema(
                name="create_reservation",
                description=(
                    "Book a table. The first call returns the details to read back; call "
                    "again with caller_confirmed true once the caller says yes."
                ),
                properties={
                    "customer_name": {"type": "string"},
                    "phone": {"type": "string"},
                    **slot,
                    "special_requests": {"type": "string"},
                    "caller_confirmed": _CONFIRMED,
                },
                required=["customer_name", "date", "time", "guests"],
                handler=self._wrap(self.create_reservation),
            ),
            FunctionSchema(
                name="get_reservation",
                description="Find a booking by reference, or by the name and phone used.",
                properties={
                    "booking_reference": {"type": "string"},
                    "customer_name": {"type": "string"},
                    "phone": {"type": "string"},
                },
                required=[],
                handler=self._wrap(self.get_reservation),
            ),
            FunctionSchema(
                name="cancel_reservation",
                description=(
                    "Cancel a booking. The first call returns the booking to read back; "
                    "call again with caller_confirmed true once the caller says yes."
                ),
                properties={
                    "booking_reference": {"type": "string"},
                    "caller_confirmed": _CONFIRMED,
                },
                required=["booking_reference"],
                handler=self._wrap(self.cancel_reservation),
            ),
        ]

    def _wrap(self, handler: Handler) -> Callable[[FunctionCallParams], Any]:
        async def run(params: FunctionCallParams) -> None:
            started = time.perf_counter()
            try:
                result = await handler(dict(params.arguments or {}), _caller_turns(params))
            except Exception:
                logger.exception("Tool {} failed", params.function_name)
                result = _error(
                    "internal_error",
                    "Something went wrong on our side. Apologise and offer to try again.",
                )
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            logger.info(
                "Tool call | name={} ok={} error_code={} duration_ms={}",
                params.function_name,
                result.get("ok"),
                result.get("error_code"),
                duration_ms,
            )
            if self._observer:
                self._observer(params.function_name, result, duration_ms)
            await params.result_callback(result)

        return run

    def _slot(self, args: dict[str, Any]) -> BookingSlot | RuleViolation:
        return validate_booking_request(
            self._profile,
            str(args.get("date", "")),
            str(args.get("time", "")),
            _as_int(args.get("guests")),
            self._clock(),
        )

    def _confirmed(self, tool: str, details: tuple[Any, ...], args: dict, turn: int) -> bool:
        """True if these details were staged in an earlier caller turn and confirmed now.

        Otherwise (re)stages them, so they must be read back and confirmed first.
        """
        staged = self._staged.get(tool)
        if (
            staged is not None
            and staged[0] == details
            and turn > staged[1]
            and args.get("caller_confirmed") is True
        ):
            del self._staged[tool]
            return True
        if staged is None or staged[0] != details:
            self._staged[tool] = (details, turn)
        return False

    # --- Tools ------------------------------------------------------------------

    async def get_restaurant_info(self, args: dict[str, Any], turn: int = 0) -> dict[str, Any]:
        p = self._profile
        return {
            "ok": True,
            "name": p.name,
            "cuisine": p.cuisine,
            "description": p.description,
            "address": p.address,
            "phone": p.phone,
            "email": p.email,
            "opening_hours": p.hours_summary(),
            "last_seating": f"{p.rules.last_seating_minutes_before_close} minutes before closing",
            "max_party_size_by_phone": p.rules.max_guests,
            "services": p.services,
            "policies": p.policies,
        }

    async def check_availability(self, args: dict[str, Any], turn: int = 0) -> dict[str, Any]:
        slot = self._slot(args)
        if isinstance(slot, RuleViolation):
            return slot.as_result()
        return {"requested": slot.as_dict(), **await self._backend.check_availability(slot)}

    async def get_available_tables(self, args: dict[str, Any], turn: int = 0) -> dict[str, Any]:
        slot = self._slot(args)
        if isinstance(slot, RuleViolation):
            return slot.as_result()
        return {"requested": slot.as_dict(), **await self._backend.get_available_tables(slot)}

    async def create_reservation(self, args: dict[str, Any], turn: int = 0) -> dict[str, Any]:
        name = _clean_text(args.get("customer_name"), MAX_NAME_CHARS)
        if not name:
            return _error("missing_name", "Ask the caller for the name for the booking.")
        phone = None
        if args.get("phone"):
            phone = _clean_phone(args.get("phone"))
            if phone is None:
                return _error("invalid_phone", "That phone number looks incomplete. Ask again.")
        if self._profile.rules.require_phone and phone is None:
            return _error("missing_phone", "A phone number is required. Ask the caller for one.")
        slot = self._slot(args)
        if isinstance(slot, RuleViolation):
            return slot.as_result()
        reservation = NewReservation(
            slot=slot,
            customer_name=name,
            phone=phone,
            special_requests=_clean_text(args.get("special_requests"), MAX_REQUEST_CHARS),
        )
        details = (name.lower(), phone, slot, reservation.special_requests)
        if not self._confirmed("create_reservation", details, args, turn):
            return {
                "ok": False,
                "error_code": "needs_confirmation",
                "message": (
                    "Not booked yet. Read these details back to the caller and ask them to "
                    "confirm. If they say yes, call create_reservation again with the same "
                    "details and caller_confirmed true."
                ),
                "details_to_confirm": {
                    "customer_name": name,
                    **slot.as_dict(),
                    "phone": phone,
                    "special_requests": reservation.special_requests,
                },
            }
        # The backend re-checks availability atomically before committing.
        return await self._backend.create_reservation(reservation)

    async def get_reservation(self, args: dict[str, Any], turn: int = 0) -> dict[str, Any]:
        reference = _clean_reference(args.get("booking_reference"))
        name = _clean_text(args.get("customer_name"), MAX_NAME_CHARS)
        phone = _clean_phone(args.get("phone"))
        if args.get("booking_reference") and reference is None:
            return _error("invalid_reference", "That booking reference doesn't look right.")
        if reference is None and not (name and phone):
            return _error(
                "missing_lookup_details",
                "Ask for the booking reference, or the name and phone number used to book.",
            )
        return await self._backend.get_reservation(reference, name, phone)

    async def cancel_reservation(self, args: dict[str, Any], turn: int = 0) -> dict[str, Any]:
        reference = _clean_reference(args.get("booking_reference"))
        if reference is None:
            return _error("invalid_reference", "Find the booking with get_reservation first.")
        if self._confirmed("cancel_reservation", (reference,), args, turn):
            return await self._backend.cancel_reservation(reference)
        # Show the booking that would be cancelled, so it can be read back.
        found = await self._backend.get_reservation(reference, None, None)
        if not found.get("ok"):
            self._staged.pop("cancel_reservation", None)
            return found
        return {
            "ok": False,
            "error_code": "needs_confirmation",
            "message": (
                "Not cancelled yet. Read this booking back and ask the caller to confirm "
                "the cancellation. If they say yes, call cancel_reservation again with "
                "caller_confirmed true."
            ),
            "booking": found,
        }


def _caller_turns(params: FunctionCallParams) -> int:
    """How many times the caller has spoken so far (user messages in the context)."""
    context = getattr(params, "context", None)
    if context is None:
        return 0
    return sum(1 for m in context.get_messages() if isinstance(m, dict) and m.get("role") == "user")
