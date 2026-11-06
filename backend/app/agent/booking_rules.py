"""Validation of reservation requests against the restaurant's rules.

Pure functions with no I/O, so they are cheap to unit-test. The LLM sends dates
and times as strings. Everything it sends is parsed and checked here before any
availability lookup or booking, and each failure comes back with a stable code
and a message the assistant can turn into a spoken explanation.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from app.agent.restaurant import WEEKDAYS, RestaurantProfile


@dataclass(frozen=True)
class BookingSlot:
    """A validated request: a local date and time for a number of guests."""

    date: date
    time: time
    guests: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "weekday": WEEKDAYS[self.date.weekday()],
            "time": self.time.strftime("%H:%M"),
            "guests": self.guests,
        }


@dataclass(frozen=True)
class RuleViolation:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_result(self) -> dict[str, Any]:
        return {"ok": False, "error_code": self.code, "message": self.message, **self.details}


_TIME_RE = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*([ap])?\.?\s*m?\.?\s*$", re.I)


def parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None


def parse_time(value: str) -> time | None:
    """Accept '19:00', '7:00 PM', '7pm', '19:00:00'."""
    match = _TIME_RE.match(value or "")
    if not match:
        return None
    hour, minute, meridiem = int(match.group(1)), int(match.group(2) or 0), match.group(3)
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem.lower() == "p" else 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def _fmt(t: time) -> str:
    return t.strftime("%H:%M")


def validate_booking_request(
    profile: RestaurantProfile,
    date_value: str,
    time_value: str,
    guests: Any,
    now: datetime,
) -> BookingSlot | RuleViolation:
    """Check a requested date, time and party size. `now` is restaurant-local time."""
    rules = profile.rules

    if not isinstance(guests, int) or isinstance(guests, bool):
        return RuleViolation("invalid_guest_count", "The number of guests must be a whole number.")
    if guests < rules.min_guests:
        return RuleViolation("invalid_guest_count", "The party must have at least one guest.")
    if guests > rules.max_guests:
        return RuleViolation(
            "party_too_large",
            f"Phone bookings are limited to {rules.max_guests} guests. Larger parties are "
            f"arranged by email at {profile.email}.",
            {"max_guests": rules.max_guests},
        )

    day = parse_date(date_value)
    if day is None:
        return RuleViolation("invalid_date", "The date must be in YYYY-MM-DD format.")
    at = parse_time(time_value)
    if at is None:
        return RuleViolation("invalid_time", "The time must be in 24-hour HH:MM format.")

    today = now.date()
    if day < today:
        return RuleViolation("date_in_past", "That date has already passed.")
    last_day = today + timedelta(days=rules.booking_window_days)
    if day > last_day:
        return RuleViolation(
            "beyond_booking_window",
            f"Bookings open {rules.booking_window_days} days ahead, up to {last_day.isoformat()}.",
            {"latest_date": last_day.isoformat()},
        )

    hours = profile.hours_for(day.weekday())
    weekday = WEEKDAYS[day.weekday()]
    if hours is None:
        return RuleViolation(
            "closed_on_date",
            f"The restaurant is closed on {weekday}s.",
            {"weekday": weekday, "opening_hours": profile.hours_summary()},
        )

    close_dt = datetime.combine(day, hours.closes)
    last_seating = (close_dt - timedelta(minutes=rules.last_seating_minutes_before_close)).time()
    if at < hours.opens or at > last_seating:
        return RuleViolation(
            "outside_opening_hours",
            f"On {weekday} tables can be booked from {_fmt(hours.opens)} to "
            f"{_fmt(last_seating)} (closing at {_fmt(hours.closes)}).",
            {
                "weekday": weekday,
                "first_seating": _fmt(hours.opens),
                "last_seating": _fmt(last_seating),
            },
        )

    requested = datetime.combine(day, at, tzinfo=now.tzinfo)
    if requested < now + timedelta(minutes=rules.min_notice_minutes):
        return RuleViolation(
            "too_soon",
            f"Bookings need at least {rules.min_notice_minutes} minutes' notice."
            if requested >= now
            else "That time has already passed today.",
        )

    return BookingSlot(date=day, time=at, guests=guests)
