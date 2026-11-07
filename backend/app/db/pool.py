"""The connection pool.

Supabase notes:
- The free plan's direct host is IPv6-only, so the usual URL is the Supavisor
  pooler. Its transaction mode (port 6543) does not support prepared statements,
  so the statement cache is disabled. That works in session mode (5432) too.
- TLS is required for remote hosts and skipped for localhost.
"""

import json
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

import asyncpg
from loguru import logger

from app.config import Settings

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass
class Database:
    pool: asyncpg.Pool
    # The restaurant this deployment serves (restaurants.slug).
    restaurant_slug: str

    async def close(self) -> None:
        await self.pool.close()


async def _init_connection(conn: asyncpg.Connection) -> None:
    for kind in ("json", "jsonb"):
        await conn.set_type_codec(kind, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


def normalize_dsn(dsn: str) -> str:
    """Percent-encode the user and password, so passwords containing `@`, `#`, `/` or `:`
    work as typed. Everything before the *last* `@` is the credentials (a standard URL
    parser stops at `#` or `/` inside the password). Already-encoded passwords are left
    as they are (decoded, then encoded again)."""
    scheme, sep, rest = dsn.partition("://")
    userinfo, at, location = rest.rpartition("@")
    if not sep or not at:
        return dsn
    user, colon, password = userinfo.partition(":")
    encoded = quote(unquote(user), safe="")
    if colon:
        encoded += ":" + quote(unquote(password), safe="")
    return f"{scheme}://{encoded}@{location}"


async def create_database(settings: Settings) -> Database:
    """Open the pool. Raises if the database is unreachable or rejects the credentials."""
    assert settings.database_url is not None
    dsn = normalize_dsn(settings.database_url.get_secret_value())
    host = urlsplit(dsn).hostname or ""
    pool = await asyncpg.create_pool(
        dsn,
        min_size=1,
        max_size=settings.db_pool_max_size,
        statement_cache_size=0,
        command_timeout=settings.db_command_timeout_secs,
        ssl=None if host in LOCAL_HOSTS else "require",
        init=_init_connection,
    )
    logger.info("Database connected | host={}", host)
    return Database(pool=pool, restaurant_slug=settings.restaurant_slug)
