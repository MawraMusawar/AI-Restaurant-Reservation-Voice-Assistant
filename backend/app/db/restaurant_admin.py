"""Restaurant settings: details, rules, hours, tables, assistant personality.

Changes apply to the next call (each call loads the profile when it starts).
Tables are deactivated rather than deleted, because past reservations reference them.
"""

from datetime import time
from typing import Any

import asyncpg

RESTAURANT_FIELDS = (
    "name",
    "cuisine",
    "description",
    "address",
    "phone",
    "email",
    "timezone",
    "min_party_size",
    "max_party_size",
    "booking_window_days",
    "last_seating_minutes_before_close",
    "min_notice_minutes",
    "dining_duration_minutes",
    "slot_interval_minutes",
    "require_phone",
    "services",
    "policies",
)
ASSISTANT_FIELDS = ("assistant_name", "greeting", "personality", "voice_id")


async def get_settings_bundle(conn: asyncpg.Connection, slug: str) -> dict[str, Any] | None:
    restaurant = await conn.fetchrow("select * from restaurants where slug = $1", slug)
    if restaurant is None:
        return None
    rid = restaurant["id"]
    hours: list[dict[str, str] | None] = [None] * 7
    for h in await conn.fetch(
        "select weekday, opens_at, closes_at from restaurant_hours where restaurant_id = $1", rid
    ):
        hours[h["weekday"]] = {
            "opens": h["opens_at"].strftime("%H:%M"),
            "closes": h["closes_at"].strftime("%H:%M"),
        }
    tables = await conn.fetch(
        """select id, label, seats, min_party_size, area, is_active from restaurant_tables
        where restaurant_id = $1 order by is_active desc, seats, label""",
        rid,
    )
    assistant = await conn.fetchrow(
        "select * from assistant_settings where restaurant_id = $1", rid
    )
    return {
        "restaurant": {
            "id": str(rid),
            "slug": slug,
            **{f: restaurant[f] for f in RESTAURANT_FIELDS},
        },
        "hours": hours,
        "tables": [{**dict(t), "id": str(t["id"])} for t in tables],
        "assistant": {f: (assistant[f] if assistant else None) for f in ASSISTANT_FIELDS},
    }


async def restaurant_id(conn: asyncpg.Connection, slug: str) -> Any:
    return await conn.fetchval("select id from restaurants where slug = $1", slug)


async def update_restaurant(conn: asyncpg.Connection, slug: str, values: dict[str, Any]) -> None:
    columns = [f for f in RESTAURANT_FIELDS if f in values]
    assignments = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(columns))
    await conn.execute(
        f"update restaurants set {assignments} where slug = $1",
        slug,
        *(values[c] for c in columns),
    )


async def replace_hours(
    conn: asyncpg.Connection, slug: str, hours: list[tuple[time, time] | None]
) -> None:
    async with conn.transaction():
        rid = await restaurant_id(conn, slug)
        await conn.execute("delete from restaurant_hours where restaurant_id = $1", rid)
        await conn.executemany(
            """insert into restaurant_hours (restaurant_id, weekday, opens_at, closes_at)
            values ($1, $2, $3, $4)""",
            [(rid, day, h[0], h[1]) for day, h in enumerate(hours) if h is not None],
        )


async def update_assistant(conn: asyncpg.Connection, slug: str, values: dict[str, Any]) -> None:
    rid = await restaurant_id(conn, slug)
    await conn.execute(
        """
        insert into assistant_settings (restaurant_id, assistant_name, greeting, personality,
                                        voice_id)
        values ($1, $2, $3, $4, $5)
        on conflict (restaurant_id) do update set
            assistant_name = excluded.assistant_name, greeting = excluded.greeting,
            personality = excluded.personality, voice_id = excluded.voice_id
        """,
        rid,
        values["assistant_name"],
        values.get("greeting"),
        values["personality"],
        values.get("voice_id"),
    )


async def add_table(conn: asyncpg.Connection, slug: str, values: dict[str, Any]) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        insert into restaurant_tables (restaurant_id, label, seats, min_party_size, area)
        values ((select id from restaurants where slug = $1), $2, $3, $4, $5)
        returning id, label, seats, min_party_size, area, is_active
        """,
        slug,
        values["label"],
        values["seats"],
        values["min_party_size"],
        values["area"],
    )
    return {**dict(row), "id": str(row["id"])}


async def update_table(
    conn: asyncpg.Connection, slug: str, table_id: str, values: dict[str, Any]
) -> dict[str, Any] | None:
    columns = [c for c in ("label", "seats", "min_party_size", "area", "is_active") if c in values]
    assignments = ", ".join(f"{c} = ${i + 3}" for i, c in enumerate(columns))
    row = await conn.fetchrow(
        f"""
        update restaurant_tables set {assignments}
        where id = $2 and restaurant_id = (select id from restaurants where slug = $1)
        returning id, label, seats, min_party_size, area, is_active
        """,
        slug,
        table_id,
        *(values[c] for c in columns),
    )
    return {**dict(row), "id": str(row["id"])} if row else None
