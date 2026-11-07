"""Shared fixtures for the database tests."""

import secrets
from collections.abc import AsyncIterator

import pytest

from app.config import get_settings
from app.db import Database, create_database
from app.db.migrate import migrate


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    database = await create_database(get_settings())
    await migrate(database.pool)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def restaurant(db: Database) -> AsyncIterator[str]:
    """A throwaway restaurant (random slug, two tables: T2 seats 1-2, T4 seats 1-4, open
    12:00-23:00 every day), deleted afterwards. Returns its slug."""
    slug = "test-" + secrets.token_hex(4)
    async with db.pool.acquire() as conn, conn.transaction():
        rid = await conn.fetchval(
            """insert into restaurants (slug, name, timezone, booking_reference_prefix,
                   min_notice_minutes, dining_duration_minutes)
               values ($1, 'Test Bistro', 'Asia/Karachi', 'TS', 0, 90) returning id""",
            slug,
        )
        for weekday in range(7):
            await conn.execute(
                "insert into restaurant_hours (restaurant_id, weekday, opens_at, closes_at) "
                "values ($1, $2, '12:00', '23:00')",
                rid,
                weekday,
            )
        await conn.execute(
            "insert into restaurant_tables (restaurant_id, label, seats, area) values "
            "($1, 'T2', 2, 'window'), ($1, 'T4', 4, 'main')",
            rid,
        )
    try:
        yield slug
    finally:
        async with db.pool.acquire() as conn:
            await conn.execute("delete from restaurants where slug = $1", slug)
