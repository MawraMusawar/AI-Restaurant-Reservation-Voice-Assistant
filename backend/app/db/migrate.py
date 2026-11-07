"""Apply the SQL migrations in `supabase/migrations/` (and optionally the seed).

    uv run python -m app.db.migrate            # apply pending migrations
    uv run python -m app.db.migrate --seed     # ...then load supabase/seed.sql
    uv run python -m app.db.migrate --status   # list applied / pending

Each migration runs in its own transaction and is recorded in `schema_migrations`,
so re-running is safe. The seed is idempotent. The same files can also be pasted
into the Supabase SQL editor.
"""

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg
from loguru import logger

from app.config import get_settings
from app.db.pool import create_database

SUPABASE_DIR = Path(__file__).resolve().parents[3] / "supabase"
MIGRATIONS_DIR = SUPABASE_DIR / "migrations"
SEED_FILE = SUPABASE_DIR / "seed.sql"


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


async def applied_versions(conn: asyncpg.Connection) -> set[str]:
    exists = await conn.fetchval("select to_regclass('public.schema_migrations') is not null")
    if not exists:
        # DDL only on first run: ALTER TABLE takes an exclusive lock every time it runs.
        await conn.execute(
            """
            create table schema_migrations (
                version text primary key,
                applied_at timestamptz not null default now()
            );
            alter table schema_migrations enable row level security;
            """
        )
    return {r["version"] for r in await conn.fetch("select version from schema_migrations")}


async def migrate(pool: asyncpg.Pool, *, seed: bool = False) -> list[str]:
    """Apply pending migrations; return the versions applied."""
    applied: list[str] = []
    async with pool.acquire() as conn:
        done = await applied_versions(conn)
        for path in migration_files():
            if path.stem in done:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute("insert into schema_migrations (version) values ($1)", path.stem)
            logger.info("Applied migration {}", path.name)
            applied.append(path.stem)
        if seed:
            async with conn.transaction():
                await conn.execute(SEED_FILE.read_text(encoding="utf-8"))
            logger.info("Loaded seed data from {}", SEED_FILE.name)
    return applied


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", action="store_true", help="also load supabase/seed.sql")
    parser.add_argument("--status", action="store_true", help="only list migrations")
    args = parser.parse_args()

    settings = get_settings()
    if settings.database_url is None:
        logger.error("DATABASE_URL is not set in backend/.env")
        return 1
    db = await create_database(settings)
    try:
        if args.status:
            async with db.pool.acquire() as conn:
                done = await applied_versions(conn)
            for path in migration_files():
                logger.info("{} {}", "applied" if path.stem in done else "PENDING", path.name)
            return 0
        applied = await migrate(db.pool, seed=args.seed)
        logger.info("Up to date ({} applied now)", len(applied))
        return 0
    finally:
        await db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
