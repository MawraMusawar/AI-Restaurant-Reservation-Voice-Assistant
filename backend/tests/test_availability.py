"""Availability logic: table fit, overlap, alternatives (pure functions, no database)."""

from datetime import date, datetime, time, timedelta

from app.agent.availability import (
    Booking,
    TableInfo,
    alternative_times,
    booking_period,
    day_bounds,
    free_tables,
    local_datetime,
)
from app.agent.booking_rules import BookingSlot
from app.agent.restaurant import DEMO_RESTAURANT as P

SATURDAY = date(2026, 11, 7)  # open 12:00-23:30, last seating 22:30
NOW = datetime(2026, 11, 6, 15, 0, tzinfo=P.tz)  # Friday afternoon

T2 = TableInfo("t2", "T2", seats=2, min_party_size=1, area="main")
T4 = TableInfo("t4", "T4", seats=4, min_party_size=1, area="main")
T4T = TableInfo("t4t", "T4T", seats=4, min_party_size=2, area="terrace")
T8 = TableInfo("t8", "T8", seats=8, min_party_size=5, area="main")
TABLES = [T8, T4T, T2, T4]


def at(hh: int, mm: int = 0, day: date = SATURDAY) -> datetime:
    return local_datetime(P, day, time(hh, mm))


def booking(table: TableInfo, hh: int, mm: int = 0) -> Booking:
    start = at(hh, mm)
    return Booking(table.id, start, start + timedelta(minutes=90))


def test_booking_period_uses_restaurant_local_time_and_dining_duration() -> None:
    start, end = booking_period(P, BookingSlot(SATURDAY, time(19, 0), 2))
    assert start.utcoffset() == timedelta(hours=5)  # Asia/Karachi
    assert start.astimezone().isoformat() == start.astimezone().isoformat()
    assert end - start == timedelta(minutes=90)
    assert start.hour == 19 and start.tzinfo is not None


def test_day_bounds_cover_late_bookings_running_past_midnight() -> None:
    start, end = day_bounds(P, SATURDAY)
    assert start == at(0, 0)
    assert end == at(0, 0) + timedelta(days=1, minutes=90)


def test_best_fit_table_first() -> None:
    free = free_tables(TABLES, [], 2, at(19), at(20, 30))
    assert [t.label for t in free] == ["T2", "T4", "T4T"]  # smallest first; T8 needs 5+


def test_minimum_party_size_is_respected() -> None:
    assert [t.label for t in free_tables(TABLES, [], 1, at(19), at(20, 30))] == ["T2", "T4"]
    assert [t.label for t in free_tables(TABLES, [], 6, at(19), at(20, 30))] == ["T8"]
    assert free_tables(TABLES, [], 9, at(19), at(20, 30)) == []


def test_overlapping_booking_makes_the_table_busy() -> None:
    bookings = [booking(T2, 18, 0)]  # 18:00-19:30
    # 19:00 overlaps 18:00-19:30 -> T2 busy.
    assert [t.label for t in free_tables(TABLES, bookings, 2, at(19), at(20, 30))] == [
        "T4",
        "T4T",
    ]


def test_back_to_back_bookings_do_not_overlap() -> None:
    bookings = [booking(T2, 17, 30)]  # 17:30-19:00, half-open range
    free = free_tables(TABLES, bookings, 2, at(19), at(20, 30))
    assert free[0].label == "T2"


def test_alternatives_are_the_nearest_free_times_in_order() -> None:
    # Only one table for a party of 3 or 4 (T4 and T4T), both taken 18:00-19:30.
    tables = [T4, T4T]
    bookings = [booking(T4, 18), booking(T4T, 18)]
    slot = BookingSlot(SATURDAY, time(19, 0), 4)
    alts = alternative_times(P, slot, tables, bookings, NOW)
    # Free: up to 16:30 (ends as the 18:00 bookings start) and from 19:30. The three
    # nearest to 19:00 are 19:30, 19:45 and 20:00; 16:30 is 2.5 hours away.
    assert alts == [time(19, 30), time(19, 45), time(20, 0)]
    # With more allowed, the earlier free time appears too; 16:45 would overlap 18:00.
    many = alternative_times(P, slot, tables, bookings, NOW, limit=20)
    assert time(16, 30) in many and time(16, 45) not in many and many == sorted(many)


def test_alternatives_respect_last_seating_and_notice() -> None:
    tables = [T2]
    late = [booking(T2, 21, 0)]  # 21:00-22:30
    slot = BookingSlot(SATURDAY, time(22, 0), 2)
    alts = alternative_times(P, slot, tables, late, NOW, limit=10)
    assert time(22, 45) not in alts  # after last seating (22:30)
    assert all(t <= time(22, 30) for t in alts)
    assert time(19, 30) in alts  # 19:30-21:00 is free, before the 21:00 booking

    # Today, 30 minutes' notice: nothing before 15:30 when it's 15:00.
    friday = NOW.date()
    today = alternative_times(P, BookingSlot(friday, time(16, 0), 2), tables, [], NOW, limit=50)
    assert min(today) == time(15, 30)


def test_no_alternatives_on_a_closed_day() -> None:
    monday = date(2026, 11, 9)
    assert alternative_times(P, BookingSlot(monday, time(19, 0), 2), TABLES, [], NOW) == []


def test_database_url_passwords_with_special_characters_are_encoded() -> None:
    from app.db.pool import normalize_dsn

    host = "aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres"
    assert (
        normalize_dsn(f"postgresql://postgres.ref:p@ss#w/rd@{host}")
        == f"postgresql://postgres.ref:p%40ss%23w%2Frd@{host}"
    )
    # Already encoded: unchanged. No credentials: unchanged.
    encoded = f"postgresql://postgres.ref:p%40ss@{host}"
    assert normalize_dsn(encoded) == encoded
    assert normalize_dsn("postgresql://localhost/db") == "postgresql://localhost/db"
