"""Sample reservations for the client demo, so the dashboard and lists aren't empty.

    uv run python -m app.db.demo_data            # add (or refresh) the sample bookings
    uv run python -m app.db.demo_data --remove   # take them out again
    uv run python -m app.db.demo_data --clear-call-logs   # also delete ALL call logs

Sample bookings use references `<PREFIX>-DEMO01`..`DEMO12`. Real references never
contain 0, 1 or O (see REFERENCE_ALPHABET), so samples can't collide with real
bookings and are removed precisely. Dates are relative to today in the restaurant's
time zone: some today (earlier ones marked completed), the rest over the next days.
Samples are marked as staff-entered (source "admin"); no call logs are invented, those
come only from real calls.
"""

import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta

import asyncpg
from loguru import logger

from app.agent.availability import Booking, TableInfo, booking_period, free_tables
from app.agent.booking_rules import RuleViolation, validate_booking_request
from app.agent.restaurant import RestaurantProfile
from app.config import get_settings
from app.db.pool import create_database
from app.db.restaurants import load_profile, load_tables

# (day offset, time, guests, name, phone, special request, status override)
# Sample 5 takes the only table for 9-10 guests (T12), so asking for 10 people at that
# time is always full: the demo's "alternative times" scenario.
FULL_SLOT_SAMPLE = 5
SAMPLES: list[tuple[int, str, int, str, str | None, str | None, str | None]] = [
    (0, "13:00", 2, "Ayesha Khan", "+923001110001", None, None),
    (0, "19:00", 4, "Bilal Ahmed", "+923001110002", "Quiet table, please", None),
    (0, "20:30", 6, "Fatima Malik", None, "Birthday celebration", None),
    (1, "19:30", 2, "Hamza Sheikh", "+923001110004", None, None),
    (1, "20:00", 10, "Sara Javed", "+923001110005", "Family dinner, two high chairs", None),
    (2, "13:30", 3, "Usman Tariq", None, None, "cancelled"),
    (3, "19:00", 2, "Zainab Raza", "+923001110007", "Vegetarian menu", None),
    (3, "21:00", 5, "Omar Farooq", "+923001110008", None, None),
    (4, "18:30", 4, "Hina Qureshi", None, None, None),
    (5, "20:00", 10, "Ali Hassan", "+923001110010", "Office dinner", None),
    (5, "19:15", 2, "Mariam Siddiqui", "+923001110011", None, "cancelled"),
    (6, "19:45", 3, "Danish Iqbal", None, "Anniversary dinner", None),
]


def demo_reference(prefix: str, n: int) -> str:
    return f"{prefix}-DEMO{n:02d}"


def _open_day(profile: RestaurantProfile, day: date) -> date:
    """Move to the next day the restaurant is open (sample dates shouldn't land on Monday)."""
    while profile.hours_for(day.weekday()) is None:
        day += timedelta(days=1)
    return day


async def remove(conn: asyncpg.Connection, profile: RestaurantProfile) -> int:
    pattern = f"{profile.booking_reference_prefix}-DEMO%"
    customers = await conn.fetch(
        "select distinct customer_id from reservations "
        "where restaurant_id = $1 and booking_reference like $2",
        profile.id,
        pattern,
    )
    deleted = await conn.execute(
        "delete from reservations where restaurant_id = $1 and booking_reference like $2",
        profile.id,
        pattern,
    )
    # Customers that only existed for samples.
    await conn.execute(
        """delete from customers c where c.id = any($1::uuid[])
           and not exists (select 1 from reservations r where r.customer_id = c.id)""",
        [r["customer_id"] for r in customers],
    )
    return int(deleted.split()[-1])


async def clear_call_logs(conn: asyncpg.Connection, profile: RestaurantProfile) -> int:
    """Delete every call log of this restaurant (transcripts go with them). Used once before a
    demo to drop development test calls; reservations those calls made are kept."""
    deleted = await conn.execute("delete from voice_sessions where restaurant_id = $1", profile.id)
    return int(deleted.split()[-1])


async def add(conn: asyncpg.Connection, profile: RestaurantProfile) -> list[str]:
    tables = await load_tables(conn, profile.id)
    now = profile.now()
    rows = await conn.fetch(
        "select table_id, starts_at, ends_at from reservations "
        "where restaurant_id = $1 and status = 'confirmed'",
        profile.id,
    )
    booked = [Booking(str(r["table_id"]), r["starts_at"], r["ends_at"]) for r in rows]
    created: list[str] = []
    for n, (offset, at, guests, name, phone, request, status) in enumerate(SAMPLES, start=1):
        day = _open_day(profile, now.date() + timedelta(days=offset))
        # Check against the real rules from midnight, so today's earlier samples still pass.
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        slot = validate_booking_request(profile, day.isoformat(), at, guests, midnight)
        if isinstance(slot, RuleViolation):
            logger.warning("Sample {} ({} at {}) breaks a rule: {}", n, day, at, slot.message)
            continue
        start, end = booking_period(profile, slot)
        free = free_tables(tables, booked, guests, start, end)
        if not free:
            logger.warning("No free table for sample {} ({} at {}); skipped", n, day, at)
            continue
        table: TableInfo = free[0]
        final_status = status or ("completed" if end <= now else "confirmed")
        customer = await conn.fetchval(
            """insert into customers (restaurant_id, name, phone) values ($1, $2, $3)
               on conflict (restaurant_id, phone) where phone is not null
               do update set phone = excluded.phone returning id""",
            profile.id,
            name,
            phone,
        )
        reference = demo_reference(profile.booking_reference_prefix, n)
        await conn.execute(
            """insert into reservations (restaurant_id, table_id, customer_id, booking_reference,
                   party_size, starts_at, ends_at, status, special_requests, source,
                   cancelled_at, created_at)
               values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)""",
            profile.id,
            table.id,
            customer,
            reference,
            guests,
            start,
            end,
            final_status,
            request,
            # Entered as staff bookings: no call created them, so none claims to be "voice".
            "admin",
            datetime.now(start.tzinfo) if final_status == "cancelled" else None,
            # Spread "made at" times over the past days, so the activity feed looks natural.
            datetime.now(start.tzinfo) - timedelta(hours=3 * n + 1),
        )
        if final_status == "confirmed":
            booked.append(Booking(table.id, start, end))
        created.append(f"{reference}  {day} {at}  {guests}p  {name}  ({final_status})")
        if n == FULL_SLOT_SAMPLE:
            created.append(
                f"Full-slot demo: ask for {guests} people on {day:%A %d %B} at {at} "
                "(that table is taken, so the assistant offers other times)"
            )
    return created


async def main() -> int:
    parser = argparse.ArgumentParser(description="Sample reservations for the demo.")
    parser.add_argument("--remove", action="store_true", help="only remove the samples")
    parser.add_argument(
        "--clear-call-logs",
        action="store_true",
        help="also delete ALL call logs of the restaurant (e.g. development test calls)",
    )
    args = parser.parse_args()
    settings = get_settings()
    if settings.database_url is None:
        logger.error("DATABASE_URL is not set in backend/.env")
        return 1
    db = await create_database(settings)
    try:
        async with db.pool.acquire() as conn, conn.transaction():
            profile = await load_profile(conn, db.restaurant_slug)
            removed = await remove(conn, profile)
            logger.info("Removed {} existing sample reservation(s)", removed)
            if args.clear_call_logs:
                logger.warning("Deleted {} call log(s)", await clear_call_logs(conn, profile))
            if not args.remove:
                for line in await add(conn, profile):
                    logger.info("Sample: {}", line)
        return 0
    finally:
        await db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
