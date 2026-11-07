"""Shared dependencies for the management API."""

from collections.abc import AsyncIterator
from typing import Annotated

import asyncpg
from fastapi import Depends, HTTPException, Request, status

from app.agent.restaurant import RestaurantProfile
from app.db import Database
from app.db.restaurants import RestaurantNotFoundError, load_profile

NO_DATABASE = "The reservation database is not connected (check DATABASE_URL and the backend log)."


def get_database(request: Request) -> Database:
    db: Database | None = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_DATABASE)
    return db


DbDep = Annotated[Database, Depends(get_database)]


async def get_connection(db: DbDep) -> AsyncIterator[asyncpg.Connection]:
    # Database errors anywhere in a request become a 503 (see app.main).
    async with db.pool.acquire() as conn:
        yield conn


ConnDep = Annotated[asyncpg.Connection, Depends(get_connection)]


async def get_profile(conn: ConnDep, db: DbDep) -> RestaurantProfile:
    try:
        return await load_profile(conn, db.restaurant_slug)
    except RestaurantNotFoundError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


ProfileDep = Annotated[RestaurantProfile, Depends(get_profile)]
