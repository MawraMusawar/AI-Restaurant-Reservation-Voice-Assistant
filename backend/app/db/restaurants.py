"""Load the restaurant profile (hours, rules, assistant settings) and its tables."""

import asyncpg

from app.agent.availability import TableInfo
from app.agent.restaurant import OpeningHours, ReservationRules, RestaurantProfile


class RestaurantNotFoundError(LookupError):
    pass


async def load_profile(conn: asyncpg.Connection, slug: str) -> RestaurantProfile:
    row = await conn.fetchrow(
        """
        select r.*, a.assistant_name, a.greeting, a.personality, a.voice_id
        from restaurants r
        left join assistant_settings a on a.restaurant_id = r.id
        where r.slug = $1
        """,
        slug,
    )
    if row is None:
        raise RestaurantNotFoundError(
            f"No restaurant with slug {slug!r}. Run `uv run python -m app.db.migrate --seed`."
        )
    hours: list[OpeningHours | None] = [None] * 7
    for h in await conn.fetch(
        "select weekday, opens_at, closes_at from restaurant_hours where restaurant_id = $1",
        row["id"],
    ):
        hours[h["weekday"]] = OpeningHours(opens=h["opens_at"], closes=h["closes_at"])

    return RestaurantProfile(
        id=str(row["id"]),
        booking_reference_prefix=row["booking_reference_prefix"],
        name=row["name"],
        assistant_name=row["assistant_name"] or "Sara",
        cuisine=row["cuisine"],
        description=row["description"],
        address=row["address"],
        phone=row["phone"],
        email=row["email"],
        timezone=row["timezone"],
        hours=hours,
        services=list(row["services"]),
        policies=list(row["policies"]),
        rules=ReservationRules(
            min_guests=row["min_party_size"],
            max_guests=row["max_party_size"],
            booking_window_days=row["booking_window_days"],
            last_seating_minutes_before_close=row["last_seating_minutes_before_close"],
            min_notice_minutes=row["min_notice_minutes"],
            dining_duration_minutes=row["dining_duration_minutes"],
            slot_interval_minutes=row["slot_interval_minutes"],
            require_phone=row["require_phone"],
        ),
        personality=row["personality"] or "warm, professional and concise",
        greeting=row["greeting"],
        voice_id=row["voice_id"],
    )


async def load_tables(conn: asyncpg.Connection, restaurant_id: str) -> list[TableInfo]:
    rows = await conn.fetch(
        """
        select id, label, seats, min_party_size, area
        from restaurant_tables
        where restaurant_id = $1 and is_active
        """,
        restaurant_id,
    )
    return [
        TableInfo(
            id=str(r["id"]),
            label=r["label"],
            seats=r["seats"],
            min_party_size=r["min_party_size"],
            area=r["area"],
        )
        for r in rows
    ]
