"""Reservation request validation against the demo restaurant's rules."""

from datetime import datetime, time

import pytest

from app.agent.booking_rules import (
    BookingSlot,
    RuleViolation,
    parse_time,
    validate_booking_request,
)
from app.agent.restaurant import DEMO_RESTAURANT

# Friday 6 November 2026, 21:00 in Lahore. Demo hours: closed Monday, Tue-Thu
# 12:00-22:30, Fri-Sat 12:00-23:30, Sun 12:00-21:30, last seating 60 min before close.
NOW = datetime(2026, 11, 6, 21, 0, tzinfo=DEMO_RESTAURANT.tz)


def check(date: str, at: str, guests: object = 2) -> BookingSlot | RuleViolation:
    return validate_booking_request(DEMO_RESTAURANT, date, at, guests, NOW)


def code(result: BookingSlot | RuleViolation) -> str:
    assert isinstance(result, RuleViolation), result
    return result.code


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("19:00", time(19, 0)),
        ("7:30 PM", time(19, 30)),
        ("7pm", time(19, 0)),
        ("12 pm", time(12, 0)),
        ("12:15 am", time(0, 15)),
        ("19:00:00", time(19, 0)),
    ],
)
def test_parse_time_accepts_common_forms(text: str, expected: time) -> None:
    assert parse_time(text) == expected


@pytest.mark.parametrize("text", ["25:00", "13 PM", "seven", "", "19:75"])
def test_parse_time_rejects_nonsense(text: str) -> None:
    assert parse_time(text) is None


def test_valid_request_returns_slot() -> None:
    slot = check("2026-11-07", "19:00", 4)
    assert slot == BookingSlot(date=datetime(2026, 11, 7).date(), time=time(19, 0), guests=4)
    assert isinstance(slot, BookingSlot)
    assert slot.as_dict()["weekday"] == "Saturday"


def test_closed_day_explains_and_lists_hours() -> None:
    result = check("2026-11-09", "19:00")  # Monday
    assert code(result) == "closed_on_date"
    assert isinstance(result, RuleViolation)
    assert "Monday" in result.message
    assert "Monday: closed" in result.as_result()["opening_hours"]


def test_last_seating_is_an_hour_before_closing() -> None:
    assert isinstance(check("2026-11-07", "22:30"), BookingSlot)  # Saturday, closes 23:30
    result = check("2026-11-07", "22:45")
    assert code(result) == "outside_opening_hours"
    assert isinstance(result, RuleViolation)
    assert result.details["last_seating"] == "22:30"


def test_before_opening_is_rejected() -> None:
    assert code(check("2026-11-07", "11:00")) == "outside_opening_hours"


def test_past_date_is_rejected() -> None:
    assert code(check("2026-11-05", "19:00")) == "date_in_past"


def test_today_needs_notice_and_cannot_be_in_the_past() -> None:
    assert code(check("2026-11-06", "21:10")) == "too_soon"  # 10 min notice < 30
    assert "passed" in check("2026-11-06", "20:00").message  # type: ignore[union-attr]
    assert isinstance(check("2026-11-06", "21:45"), BookingSlot)


def test_booking_window() -> None:
    assert isinstance(check("2026-12-06", "19:00"), BookingSlot)  # Sunday, day 30
    assert code(check("2026-12-07", "19:00")) == "beyond_booking_window"


@pytest.mark.parametrize(
    ("guests", "expected"),
    [
        (0, "invalid_guest_count"),
        (-2, "invalid_guest_count"),
        (True, "invalid_guest_count"),
        ("4", "invalid_guest_count"),  # strings are converted by the tool layer, not here
        (11, "party_too_large"),
    ],
)
def test_guest_count_rules(guests: object, expected: str) -> None:
    assert code(check("2026-11-07", "19:00", guests)) == expected


def test_large_party_points_to_email() -> None:
    result = check("2026-11-07", "19:00", 14)
    assert isinstance(result, RuleViolation)
    assert DEMO_RESTAURANT.email in result.message


@pytest.mark.parametrize(("date", "at"), [("next friday", "19:00"), ("2026-11-07", "seven")])
def test_unparseable_input_is_rejected(date: str, at: str) -> None:
    assert code(check(date, at)) in ("invalid_date", "invalid_time")
