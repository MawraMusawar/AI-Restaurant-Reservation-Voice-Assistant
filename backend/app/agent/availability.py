"""Table availability: pure functions over tables and existing bookings.

The database backend loads the restaurant's tables and the day's confirmed
bookings, then uses these functions to answer "is there a table?", to pick the
best one, and to suggest the nearest free times. Keeping this logic free of I/O
makes it cheap to test exhaustively. The database's exclusion constraint still
has the final say when a booking is written.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.agent.booking_rules import BookingSlot, validate_booking_request
from app.agent.restaurant import RestaurantProfile


@dataclass(frozen=True)
class TableInfo:
    id: str
    label: str
    seats: int
    min_party_size: int
    area: str

    def fits(self, guests: int) -> bool:
        return self.min_party_size <= guests <= self.seats


@dataclass(frozen=True)
class Booking:
    """A confirmed reservation occupying a table over [starts_at, ends_at)."""

    table_id: str
    starts_at: datetime
    ends_at: datetime


def local_datetime(profile: RestaurantProfile, day: date, at: time) -> datetime:
    """A local wall-clock time at the restaurant as an absolute (aware) instant."""
    return datetime.combine(day, at, tzinfo=profile.tz)


def booking_period(profile: RestaurantProfile, slot: BookingSlot) -> tuple[datetime, datetime]:
    start = local_datetime(profile, slot.date, slot.time)
    return start, start + timedelta(minutes=profile.rules.dining_duration_minutes)


def day_bounds(profile: RestaurantProfile, day: date) -> tuple[datetime, datetime]:
    """The span of bookings that can overlap any seating on `day` (local midnight to
    the next midnight plus one dining duration)."""
    start = local_datetime(profile, day, time(0, 0))
    end = start + timedelta(days=1, minutes=profile.rules.dining_duration_minutes)
    return start, end


def free_tables(
    tables: list[TableInfo],
    bookings: list[Booking],
    guests: int,
    start: datetime,
    end: datetime,
) -> list[TableInfo]:
    """Tables that fit the party and are free for [start, end), best fit first.

    Best fit = fewest seats, so a couple doesn't take the table for eight.
    """
    busy = {b.table_id for b in bookings if b.starts_at < end and start < b.ends_at}
    fitting = [t for t in tables if t.fits(guests) and t.id not in busy]
    return sorted(fitting, key=lambda t: (t.seats, t.label))


def alternative_times(
    profile: RestaurantProfile,
    slot: BookingSlot,
    tables: list[TableInfo],
    bookings: list[Booking],
    now: datetime,
    limit: int = 3,
) -> list[time]:
    """The bookable times on the same day closest to the requested one, in order.

    Candidates are every slot interval within opening hours that passes the
    booking rules (notice, last seating) and has a free table for the party.
    """
    step = timedelta(minutes=profile.rules.slot_interval_minutes)
    hours = profile.hours_for(slot.date.weekday())
    if hours is None:
        return []
    requested = local_datetime(profile, slot.date, slot.time)
    candidate = local_datetime(profile, slot.date, hours.opens)
    closing = local_datetime(profile, slot.date, hours.closes)
    duration = timedelta(minutes=profile.rules.dining_duration_minutes)

    found: list[datetime] = []
    while candidate < closing:
        if candidate != requested:
            valid = validate_booking_request(
                profile,
                slot.date.isoformat(),
                candidate.strftime("%H:%M"),
                slot.guests,
                now,
            )
            if isinstance(valid, BookingSlot) and free_tables(
                tables, bookings, slot.guests, candidate, candidate + duration
            ):
                found.append(candidate)
        candidate += step

    nearest = sorted(found, key=lambda c: (abs(c - requested), c))[:limit]
    return [c.time() for c in sorted(nearest)]
