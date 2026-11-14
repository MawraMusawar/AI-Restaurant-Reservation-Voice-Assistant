"""The demo sample data script, against the real database in a throwaway restaurant."""

import secrets
from datetime import UTC, datetime, timedelta

import pytest

from app.db import Database
from app.db.demo_data import add, clear_call_logs, remove
from app.db.restaurants import load_profile

pytestmark = pytest.mark.integration


async def test_samples_are_valid_refreshable_and_removable(db: Database, restaurant: str) -> None:
    async with db.pool.acquire() as conn:
        profile = await load_profile(conn, restaurant)
        # A real booking that must survive every step.
        table = await conn.fetchval(
            "select id from restaurant_tables where restaurant_id = $1 limit 1", profile.id
        )
        customer = await conn.fetchval(
            "insert into customers (restaurant_id, name) values ($1, 'Real Guest') returning id",
            profile.id,
        )
        start = datetime.now(UTC) + timedelta(days=20)
        await conn.execute(
            """insert into reservations (restaurant_id, table_id, customer_id, booking_reference,
                   party_size, starts_at, ends_at)
               values ($1, $2, $3, 'TS-REALAA', 2, $4, $5)""",
            profile.id,
            table,
            customer,
            start,
            start + timedelta(minutes=90),
        )

        first = await add(conn, profile)
        # The test restaurant has only a 2-seat and a 4-seat table, so larger samples are skipped.
        assert first, "no samples were added"
        rows = await conn.fetch(
            "select booking_reference, status, source from reservations "
            "where restaurant_id = $1 and booking_reference like 'TS-DEMO%'",
            profile.id,
        )
        assert len(rows) == len([line for line in first if line.startswith("TS-DEMO")])
        assert {r["source"] for r in rows} == {"admin"}
        assert {r["status"] for r in rows} <= {"confirmed", "cancelled", "completed"}

        # Refreshing replaces the samples instead of duplicating them.
        assert await remove(conn, profile) == len(rows)
        await add(conn, profile)
        assert await conn.fetchval(
            "select count(*) from reservations where booking_reference like 'TS-DEMO%' "
            "and restaurant_id = $1",
            profile.id,
        ) == len(rows)

        assert await remove(conn, profile) == len(rows)
        remaining = await conn.fetch(
            "select booking_reference from reservations where restaurant_id = $1", profile.id
        )
        assert [r["booking_reference"] for r in remaining] == ["TS-REALAA"]
        # Sample-only customers are gone; the real one stays.
        names = await conn.fetch("select name from customers where restaurant_id = $1", profile.id)
        assert [r["name"] for r in names] == ["Real Guest"]


async def test_clear_call_logs_only_touches_this_restaurant(db: Database, restaurant: str) -> None:
    session_id = "test-session-" + secrets.token_hex(4)
    async with db.pool.acquire() as conn:
        profile = await load_profile(conn, restaurant)
        try:
            await conn.execute(
                "insert into voice_sessions (id, restaurant_id, status, started_at) "
                "values ($1, $2, 'ended', now())",
                session_id,
                profile.id,
            )
            await conn.execute(
                "insert into conversation_messages (session_id, position, role, content, "
                "spoken_at) "
                "values ($1, 0, 'user', 'Hello?', now())",
                session_id,
            )
            others_before = await conn.fetchval(
                "select count(*) from voice_sessions where restaurant_id is distinct from $1",
                profile.id,
            )
            assert await clear_call_logs(conn, profile) == 1
            assert not await conn.fetchval(
                "select count(*) from conversation_messages where session_id = $1", session_id
            )
            others_after = await conn.fetchval(
                "select count(*) from voice_sessions where restaurant_id is distinct from $1",
                profile.id,
            )
            # Other restaurants' logs are untouched (a concurrent real call may only add some).
            assert others_after >= others_before
        finally:
            await conn.execute("delete from voice_sessions where id = $1", session_id)
