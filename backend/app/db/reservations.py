"""`ReservationBackend` on PostgreSQL: real availability and real bookings.

Double-booking is prevented twice:
1. In code: availability is computed from the tables and the day's confirmed
   bookings, inside the same transaction that writes the booking.
2. In the database: the `reservations_no_overlap` exclusion constraint rejects a
   second confirmed booking on a table for an overlapping time. If two callers
   race for the same table, the loser's insert fails with 23P01 and the next
   free table is tried, so the race costs a retry, never a double booking.

Hard rule: `ok: True` with a booking reference is returned only after the
transaction has committed. Any database failure becomes a `database_error`
result, which the assistant explains. It is never reported as a booking.
"""

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime
from functools import wraps
from typing import Any, Literal

import asyncpg
from loguru import logger

from app.agent.availability import (
    Booking,
    TableInfo,
    alternative_times,
    booking_period,
    day_bounds,
    free_tables,
)
from app.agent.booking_rules import BookingSlot
from app.agent.reservations import NewReservation
from app.agent.restaurant import WEEKDAYS, RestaurantProfile
from app.db.restaurants import load_tables

# No 0/O or 1/I, so references survive being read out loud.
REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
REFERENCE_LENGTH = 6
MAX_REFERENCE_ATTEMPTS = 5
MAX_LOOKUP_RESULTS = 5

DATABASE_ERROR: dict[str, Any] = {
    "ok": False,
    "error_code": "database_error",
    "message": (
        "The reservation system had a problem, so nothing was booked or changed. "
        "Apologise and suggest trying again in a moment."
    ),
}
DB_ERRORS = (
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
    OSError,
    TimeoutError,
    asyncio.TimeoutError,
)

Result = dict[str, Any]


def guarded(method: Callable[..., Awaitable[Result]]) -> Callable[..., Awaitable[Result]]:
    """Turn database failures into a `database_error` result (logged, never raised)."""

    @wraps(method)
    async def wrapper(self: "PostgresReservationBackend", *args: Any) -> Result:
        try:
            return await method(self, *args)
        except DB_ERRORS:
            logger.opt(exception=True).error("Reservation database error in {}", method.__name__)
            return dict(DATABASE_ERROR)

    return wrapper


def new_reference(prefix: str) -> str:
    code = "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(REFERENCE_LENGTH))
    return f"{prefix}-{code}"


class PostgresReservationBackend:
    def __init__(
        self,
        pool: asyncpg.Pool,
        profile: RestaurantProfile,
        session_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
        source: Literal["voice", "web", "admin"] = "voice",
    ) -> None:
        assert profile.id is not None, "the profile must come from the database"
        self._pool = pool
        self._profile = profile
        self._restaurant_id = profile.id
        self._session_id = session_id
        self._clock = clock or profile.now
        self._source = source

    # --- helpers -------------------------------------------------------------------

    async def _day_state(
        self, conn: asyncpg.Connection, slot: BookingSlot
    ) -> tuple[list[TableInfo], list[Booking]]:
        """Active tables and the confirmed bookings that can overlap seatings on that day."""
        tables = await load_tables(conn, self._restaurant_id)
        start, end = day_bounds(self._profile, slot.date)
        rows = await conn.fetch(
            """
            select table_id, starts_at, ends_at from reservations
            where restaurant_id = $1 and status = 'confirmed'
              and starts_at < $3 and ends_at > $2
            """,
            self._restaurant_id,
            start,
            end,
        )
        bookings = [Booking(str(r["table_id"]), r["starts_at"], r["ends_at"]) for r in rows]
        return tables, bookings

    def _alternatives(
        self, slot: BookingSlot, tables: list[TableInfo], bookings: list[Booking]
    ) -> list[dict[str, str]]:
        times = alternative_times(self._profile, slot, tables, bookings, self._clock())
        return [{"date": slot.date.isoformat(), "time": t.strftime("%H:%M")} for t in times]

    def _describe(self, row: asyncpg.Record) -> Result:
        local = row["starts_at"].astimezone(self._profile.tz)
        return {
            "booking_reference": row["booking_reference"],
            "customer_name": row["customer_name"],
            "guests": row["party_size"],
            "date": local.date().isoformat(),
            "weekday": WEEKDAYS[local.weekday()],
            "time": local.strftime("%H:%M"),
            "status": row["status"],
            "seating_area": row["area"],
            "special_requests": row["special_requests"],
        }

    # --- ReservationBackend -----------------------------------------------------------

    @guarded
    async def check_availability(self, slot: BookingSlot) -> Result:
        async with self._pool.acquire() as conn:
            tables, bookings = await self._day_state(conn, slot)
        start, end = booking_period(self._profile, slot)
        free = free_tables(tables, bookings, slot.guests, start, end)
        if free:
            return {"ok": True, "available": True, "seating_area": free[0].area}
        if not any(t.fits(slot.guests) for t in tables):
            return {
                "ok": True,
                "available": False,
                "alternatives": [],
                "message": "No table can seat a party of this size.",
            }
        return {
            "ok": True,
            "available": False,
            "alternatives": self._alternatives(slot, tables, bookings),
        }

    @guarded
    async def get_available_tables(self, slot: BookingSlot) -> Result:
        async with self._pool.acquire() as conn:
            tables, bookings = await self._day_state(conn, slot)
        start, end = booking_period(self._profile, slot)
        free = free_tables(tables, bookings, slot.guests, start, end)
        return {
            "ok": True,
            "tables": [{"table": t.label, "seats": t.seats, "area": t.area} for t in free],
        }

    @guarded
    async def create_reservation(self, reservation: NewReservation) -> Result:
        slot = reservation.slot
        start, end = booking_period(self._profile, slot)
        booked: asyncpg.Record | None = None
        alternatives: list[dict[str, str]] = []

        async with self._pool.acquire() as conn, conn.transaction():
            customer_id = await self._upsert_customer(conn, reservation)
            tables, bookings = await self._day_state(conn, slot)
            for table in free_tables(tables, bookings, slot.guests, start, end):
                booked = await self._insert(conn, reservation, table, customer_id, start, end)
                if booked is not None:
                    break
            if booked is None:
                alternatives = self._alternatives(slot, tables, bookings)
        # The transaction has committed here (an exception would have propagated).

        if booked is None:
            return {
                "ok": False,
                "error_code": "slot_unavailable",
                "message": (
                    "That time was just taken, so nothing was booked. Offer the alternatives."
                ),
                "alternatives": alternatives,
            }
        logger.info(
            "Reservation created | reference={} guests={} starts_at={} session_id={}",
            booked["booking_reference"],
            slot.guests,
            start.isoformat(),
            self._session_id,
        )
        return {"ok": True, "status": "confirmed", **self._describe(booked)}

    async def _upsert_customer(self, conn: asyncpg.Connection, r: NewReservation) -> Any:
        if r.phone:
            # A known phone number is the same customer; keep the latest spelling of the name.
            return await conn.fetchval(
                """
                insert into customers (restaurant_id, name, phone) values ($1, $2, $3)
                on conflict (restaurant_id, phone) where phone is not null
                do update set name = excluded.name
                returning id
                """,
                self._restaurant_id,
                r.customer_name,
                r.phone,
            )
        return await conn.fetchval(
            "insert into customers (restaurant_id, name) values ($1, $2) returning id",
            self._restaurant_id,
            r.customer_name,
        )

    async def _insert(
        self,
        conn: asyncpg.Connection,
        r: NewReservation,
        table: TableInfo,
        customer_id: Any,
        start: datetime,
        end: datetime,
    ) -> asyncpg.Record | None:
        """Book `table`, or None if another booking took it first (exclusion constraint)."""
        for _ in range(MAX_REFERENCE_ATTEMPTS):
            reference = new_reference(self._profile.booking_reference_prefix)
            try:
                async with conn.transaction():  # savepoint: a failed insert keeps the outer one
                    return await conn.fetchrow(
                        """
                        with r as (
                            insert into reservations (
                                restaurant_id, table_id, customer_id, booking_reference,
                                party_size, starts_at, ends_at, special_requests, source,
                                voice_session_id
                            ) values ($1, $2, $3, $4, $5, $6, $7, $8, $10, $9)
                            returning *
                        )
                        select r.*, c.name as customer_name, t.area
                        from r join customers c on c.id = r.customer_id
                        join restaurant_tables t on t.id = r.table_id
                        """,
                        self._restaurant_id,
                        table.id,
                        customer_id,
                        reference,
                        r.slot.guests,
                        start,
                        end,
                        r.special_requests,
                        self._session_id,
                        self._source,
                    )
            except asyncpg.exceptions.ExclusionViolationError:
                logger.info("Table {} was taken concurrently; trying the next one", table.label)
                return None
            except asyncpg.exceptions.UniqueViolationError:
                continue  # booking reference collision: draw a new one
        raise RuntimeError("Could not generate a unique booking reference")

    @guarded
    async def get_reservation(
        self, booking_reference: str | None, customer_name: str | None, phone: str | None
    ) -> Result:
        select = """
            select r.*, c.name as customer_name, t.area
            from reservations r
            join customers c on c.id = r.customer_id
            join restaurant_tables t on t.id = r.table_id
            where r.restaurant_id = $1
        """
        async with self._pool.acquire() as conn:
            if booking_reference:
                rows = await conn.fetch(
                    select + " and r.booking_reference = $2", self._restaurant_id, booking_reference
                )
            else:
                # Phone identifies the caller; the name is only a cross-check, since speech
                # recognition can misspell it.
                rows = await conn.fetch(
                    select
                    + """ and c.phone = $2 and r.status = 'confirmed' and r.ends_at > now()
                    order by r.starts_at limit $3""",
                    self._restaurant_id,
                    phone,
                    MAX_LOOKUP_RESULTS,
                )
        if not rows:
            return {
                "ok": False,
                "error_code": "not_found",
                "message": "No matching booking was found. Ask the caller to check the details.",
            }
        return {"ok": True, "reservations": [self._describe(r) for r in rows]}

    @guarded
    async def cancel_reservation(self, booking_reference: str) -> Result:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                with r as (
                    update reservations
                    set status = 'cancelled', cancelled_at = now()
                    where restaurant_id = $1 and booking_reference = $2
                      and status = 'confirmed' and starts_at > now()
                    returning *
                )
                select r.*, c.name as customer_name, t.area
                from r join customers c on c.id = r.customer_id
                join restaurant_tables t on t.id = r.table_id
                """,
                self._restaurant_id,
                booking_reference,
            )
            if row is None:
                existing = await conn.fetchrow(
                    """select status, starts_at <= now() as started from reservations
                    where restaurant_id = $1 and booking_reference = $2""",
                    self._restaurant_id,
                    booking_reference,
                )
        if row is not None:
            logger.info("Reservation cancelled | reference={}", booking_reference)
            return {"ok": True, **self._describe(row)}
        if existing is None:
            return {"ok": False, "error_code": "not_found", "message": "No such booking."}
        if existing["status"] != "confirmed":
            return {
                "ok": False,
                "error_code": "not_active",
                "message": f"That booking is already {existing['status']}.",
            }
        return {
            "ok": False,
            "error_code": "already_started",
            "message": "That booking's time has already passed, so it can't be cancelled.",
        }
