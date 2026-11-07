"""Read models for the management screens: dashboard, reservation list, call logs.

Dates and times shown to staff are in the restaurant's local time zone. Grouping
by local day happens in SQL with `AT TIME ZONE`.
"""

from datetime import date, datetime, timedelta
from typing import Any

import asyncpg

from app.agent.availability import local_datetime
from app.agent.restaurant import WEEKDAYS, RestaurantProfile

CHART_DAYS = 7
RECENT_CALLS = 6
RECENT_ACTIVITY = 8


def _local(profile: RestaurantProfile, at: datetime | None) -> datetime | None:
    return at.astimezone(profile.tz) if at is not None else None


def reservation_view(profile: RestaurantProfile, row: asyncpg.Record) -> dict[str, Any]:
    local = row["starts_at"].astimezone(profile.tz)
    return {
        "id": str(row["id"]),
        "booking_reference": row["booking_reference"],
        "customer_name": row["customer_name"],
        "phone": row["phone"],
        "guests": row["party_size"],
        "date": local.date().isoformat(),
        "weekday": WEEKDAYS[local.weekday()],
        "time": local.strftime("%H:%M"),
        "table": row["table_label"],
        "area": row["area"],
        "status": row["status"],
        "source": row["source"],
        "special_requests": row["special_requests"],
        "voice_session_id": row["voice_session_id"],
        "created_at": row["created_at"],
        "cancelled_at": row["cancelled_at"],
    }


RESERVATION_SELECT = """
    select r.*, c.name as customer_name, c.phone, t.label as table_label, t.area
    from reservations r
    join customers c on c.id = r.customer_id
    join restaurant_tables t on t.id = r.table_id
"""


async def list_reservations(
    conn: asyncpg.Connection,
    profile: RestaurantProfile,
    *,
    date_from: date | None,
    date_to: date | None,
    status: str | None,
    query: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Reservations in a local-date range (inclusive), optionally by status or a search
    over name, phone and booking reference; soonest first."""
    clauses = ["r.restaurant_id = $1"]
    args: list[Any] = [profile.id]
    if date_from is not None:
        args.append(local_datetime(profile, date_from, datetime.min.time()))
        clauses.append(f"r.starts_at >= ${len(args)}")
    if date_to is not None:
        args.append(local_datetime(profile, date_to + timedelta(days=1), datetime.min.time()))
        clauses.append(f"r.starts_at < ${len(args)}")
    if status:
        args.append(status)
        clauses.append(f"r.status = ${len(args)}")
    if query:
        args.append(f"%{query.strip().lower()}%")
        n = len(args)
        clauses.append(
            f"(lower(c.name) like ${n} or c.phone like ${n}"
            f" or lower(r.booking_reference) like ${n})"
        )
    args.append(limit)
    rows = await conn.fetch(
        RESERVATION_SELECT
        + " where "
        + " and ".join(clauses)
        + f" order by r.starts_at limit ${len(args)}",
        *args,
    )
    return [reservation_view(profile, r) for r in rows]


async def dashboard(
    conn: asyncpg.Connection, profile: RestaurantProfile, now: datetime
) -> dict[str, Any]:
    rid, tz = profile.id, profile.timezone
    today = now.date()
    today_start = local_datetime(profile, today, datetime.min.time())
    today_end = today_start + timedelta(days=1)

    totals = await conn.fetchrow(
        """
        select
            count(*) filter (where status <> 'cancelled') as active,
            count(*) filter (where status = 'cancelled') as cancelled,
            count(*) filter (where status = 'confirmed' and starts_at >= $2 and starts_at < $3)
                as today,
            coalesce(sum(party_size) filter (
                where status = 'confirmed' and starts_at >= $2 and starts_at < $3), 0)
                as today_guests,
            count(*) filter (where status = 'confirmed' and starts_at >= $4) as upcoming
        from reservations where restaurant_id = $1
        """,
        rid,
        today_start,
        today_end,
        now,
    )
    calls = await conn.fetchrow(
        """
        select count(*) as total,
               count(*) filter (where exists (
                   select 1 from reservations r
                   where r.voice_session_id = s.id and r.status <> 'cancelled'))
                   as with_booking,
               avg((metrics->>'duration_seconds')::float) as avg_duration
        from voice_sessions s where s.restaurant_id = $1
        """,
        rid,
    )

    week_end = today_start + timedelta(days=CHART_DAYS)
    booked = {
        r["day"]: (r["bookings"], r["guests"])
        for r in await conn.fetch(
            """
            select (starts_at at time zone $2)::date as day, count(*) as bookings,
                   sum(party_size) as guests
            from reservations
            where restaurant_id = $1 and status = 'confirmed'
              and starts_at >= $3 and starts_at < $4
            group by 1
            """,
            rid,
            tz,
            today_start,
            week_end,
        )
    }
    week_ago = today_start - timedelta(days=CHART_DAYS - 1)
    called = {
        r["day"]: r["calls"]
        for r in await conn.fetch(
            """
            select (started_at at time zone $2)::date as day, count(*) as calls
            from voice_sessions where restaurant_id = $1 and started_at >= $3
            group by 1
            """,
            rid,
            tz,
            week_ago,
        )
    }

    recent_calls = await conn.fetch(
        """
        select s.id, s.started_at, s.status, s.end_reason, s.error,
               (s.metrics->>'duration_seconds')::float as duration_seconds,
               (select content from conversation_messages m
                where m.session_id = s.id and m.role = 'user'
                order by position limit 1) as first_request,
               (select booking_reference from reservations r
                where r.voice_session_id = s.id order by created_at limit 1) as booking_reference
        from voice_sessions s where s.restaurant_id = $1
        order by s.started_at desc limit $2
        """,
        rid,
        RECENT_CALLS,
    )
    activity = await conn.fetch(
        RESERVATION_SELECT
        + " where r.restaurant_id = $1 order by greatest(r.created_at, r.updated_at) desc limit $2",
        rid,
        RECENT_ACTIVITY,
    )

    total_calls = calls["total"]
    return {
        "today": today.isoformat(),
        "stats": {
            "total_reservations": totals["active"],
            "cancelled_reservations": totals["cancelled"],
            "today_reservations": totals["today"],
            "today_guests": totals["today_guests"],
            "upcoming_reservations": totals["upcoming"],
            "total_calls": total_calls,
            "successful_bookings": calls["with_booking"],
            "booking_rate": round(calls["with_booking"] / total_calls, 3) if total_calls else None,
            "avg_call_seconds": round(calls["avg_duration"], 1) if calls["avg_duration"] else None,
        },
        "bookings_next_7_days": [
            {
                "date": (d := today + timedelta(days=i)).isoformat(),
                "weekday": WEEKDAYS[d.weekday()][:3],
                "bookings": booked.get(d, (0, 0))[0],
                "guests": booked.get(d, (0, 0))[1],
            }
            for i in range(CHART_DAYS)
        ],
        "calls_last_7_days": [
            {
                "date": (d := today - timedelta(days=CHART_DAYS - 1 - i)).isoformat(),
                "weekday": WEEKDAYS[d.weekday()][:3],
                "calls": called.get(d, 0),
            }
            for i in range(CHART_DAYS)
        ],
        "recent_calls": [
            {**dict(r), "started_at": _local(profile, r["started_at"])} for r in recent_calls
        ],
        "recent_activity": [reservation_view(profile, r) for r in activity],
    }


async def list_calls(
    conn: asyncpg.Connection, restaurant_id: str | None, limit: int, offset: int
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        select s.id, s.status, s.end_reason, s.started_at, s.ended_at, s.error,
               coalesce(array_length(s.errors, 1), 0) as error_count,
               (s.metrics->>'duration_seconds')::float as duration_seconds,
               (select count(*) from conversation_messages m where m.session_id = s.id)
                   as message_count,
               (select content from conversation_messages m
                where m.session_id = s.id and m.role = 'user'
                order by position limit 1) as first_request,
               (select booking_reference from reservations r
                where r.voice_session_id = s.id order by created_at limit 1) as booking_reference
        from voice_sessions s
        where s.restaurant_id = $1 or s.restaurant_id is null
        order by s.started_at desc limit $2 offset $3
        """,
        restaurant_id,
        limit,
        offset,
    )
    return [dict(r) for r in rows]


async def get_call(
    conn: asyncpg.Connection, profile: RestaurantProfile, session_id: str
) -> dict[str, Any] | None:
    row = await conn.fetchrow("select * from voice_sessions where id = $1", session_id)
    if row is None:
        return None
    messages = await conn.fetch(
        """select role, content, interrupted, spoken_at from conversation_messages
        where session_id = $1 order by position""",
        session_id,
    )
    reservations = await conn.fetch(
        RESERVATION_SELECT + " where r.voice_session_id = $1 order by r.created_at", session_id
    )
    return {
        **{k: row[k] for k in row.keys() if k not in ("restaurant_id", "created_at")},
        "transcript": [dict(m) for m in messages],
        "reservations": [reservation_view(profile, r) for r in reservations],
    }
