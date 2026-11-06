"""The interface between the assistant's tools and the reservation engine.

Milestone 6 implements `ReservationBackend` on Supabase PostgreSQL, where
availability is re-checked inside the booking transaction and constraints block
double-booking. Until then the only implementation is `OfflineReservationBackend`,
which reports that the booking system is unavailable. It never invents
availability and never reports a booking (hard rule: only the database confirms
a reservation).

Every method returns a JSON-serialisable dict with `ok: bool`. On failure it
also carries `error_code` and `message`, and on success the fields that method
documents.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from app.agent.booking_rules import BookingSlot


@dataclass(frozen=True)
class NewReservation:
    slot: BookingSlot
    customer_name: str
    phone: str | None
    special_requests: str | None


class ReservationBackend(Protocol):
    async def check_availability(self, slot: BookingSlot) -> dict[str, Any]:
        """`available: bool`, and `alternatives: [{date, time}]` when it isn't."""
        ...

    async def get_available_tables(self, slot: BookingSlot) -> dict[str, Any]:
        """`tables: [{table, seats, area}]` that can seat the party at that time."""
        ...

    async def create_reservation(self, reservation: NewReservation) -> dict[str, Any]:
        """`booking_reference` plus the stored details, only after the DB commit."""
        ...

    async def get_reservation(
        self, booking_reference: str | None, customer_name: str | None, phone: str | None
    ) -> dict[str, Any]:
        """`reservations: [...]` matching the reference or the customer details."""
        ...

    async def cancel_reservation(self, booking_reference: str) -> dict[str, Any]:
        """`booking_reference` and `status: "cancelled"`, only after the DB commit."""
        ...


BOOKING_SYSTEM_UNAVAILABLE: dict[str, Any] = {
    "ok": False,
    "error_code": "booking_system_unavailable",
    "message": (
        "The reservation system is offline, so availability cannot be checked and "
        "nothing can be booked, looked up or cancelled right now. Apologise, and suggest "
        "the caller try again a little later. Do not say a table is available or booked."
    ),
}


class OfflineReservationBackend:
    """Used until the database exists (Milestone 6): every operation is unavailable."""

    async def check_availability(self, slot: BookingSlot) -> dict[str, Any]:
        return dict(BOOKING_SYSTEM_UNAVAILABLE)

    async def get_available_tables(self, slot: BookingSlot) -> dict[str, Any]:
        return dict(BOOKING_SYSTEM_UNAVAILABLE)

    async def create_reservation(self, reservation: NewReservation) -> dict[str, Any]:
        return dict(BOOKING_SYSTEM_UNAVAILABLE)

    async def get_reservation(
        self, booking_reference: str | None, customer_name: str | None, phone: str | None
    ) -> dict[str, Any]:
        return dict(BOOKING_SYSTEM_UNAVAILABLE)

    async def cancel_reservation(self, booking_reference: str) -> dict[str, Any]:
        return dict(BOOKING_SYSTEM_UNAVAILABLE)
