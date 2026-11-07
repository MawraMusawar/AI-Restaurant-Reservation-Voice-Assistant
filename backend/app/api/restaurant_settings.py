"""Restaurant settings: details and booking rules, opening hours, tables, AI personality.

Every change takes effect on the next call (each call loads the profile at start).
"""

from datetime import time
from typing import Annotated, Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.auth import AdminDep
from app.api.deps import ConnDep, DbDep
from app.db import restaurant_admin

router = APIRouter(tags=["settings"])

Label = Annotated[str, Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9 _-]+$")]


class RestaurantIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    cuisine: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=500)
    address: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=40)
    email: str = Field(default="", max_length=120)
    timezone: str
    min_party_size: int = Field(ge=1, le=30)
    max_party_size: int = Field(ge=1, le=30)
    booking_window_days: int = Field(ge=1, le=365)
    last_seating_minutes_before_close: int = Field(ge=0, le=600)
    min_notice_minutes: int = Field(ge=0, le=1440)
    dining_duration_minutes: int = Field(ge=15, le=480)
    slot_interval_minutes: int = Field(ge=5, le=60)
    require_phone: bool
    services: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(max_length=20)
    policies: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(max_length=20)

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown time zone (use an IANA name like Asia/Karachi)") from exc
        return v

    @model_validator(mode="after")
    def _party_sizes(self) -> "RestaurantIn":
        if self.max_party_size < self.min_party_size:
            raise ValueError("max_party_size must be at least min_party_size")
        return self


class RestaurantOut(RestaurantIn):
    id: str
    slug: str


class DayHours(BaseModel):
    opens: time
    closes: time

    @model_validator(mode="after")
    def _order(self) -> "DayHours":
        if self.closes <= self.opens:
            raise ValueError("closing time must be after opening time (same day)")
        return self


class HoursIn(BaseModel):
    # Index 0 = Monday ... 6 = Sunday; null = closed.
    days: list[DayHours | None] = Field(min_length=7, max_length=7)


class TableIn(BaseModel):
    label: Label
    seats: int = Field(ge=1, le=30)
    min_party_size: int = Field(default=1, ge=1, le=30)
    area: str = Field(default="main", min_length=1, max_length=40)

    @model_validator(mode="after")
    def _fits(self) -> "TableIn":
        if self.min_party_size > self.seats:
            raise ValueError("min_party_size can't exceed seats")
        return self


class TableUpdate(BaseModel):
    label: Label | None = None
    seats: int | None = Field(default=None, ge=1, le=30)
    min_party_size: int | None = Field(default=None, ge=1, le=30)
    area: str | None = Field(default=None, min_length=1, max_length=40)
    is_active: bool | None = None


class TableOut(BaseModel):
    id: str
    label: str
    seats: int
    min_party_size: int
    area: str
    is_active: bool


class AssistantIn(BaseModel):
    assistant_name: str = Field(min_length=1, max_length=40)
    greeting: str | None = Field(default=None, max_length=300)
    personality: str = Field(min_length=1, max_length=300)
    voice_id: str | None = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9]*$")

    @field_validator("greeting", "voice_id")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v else None


class SettingsOut(BaseModel):
    restaurant: RestaurantOut
    hours: list[DayHours | None]
    tables: list[TableOut]
    assistant: AssistantIn


async def _bundle(conn: Any, slug: str) -> SettingsOut:
    data = await restaurant_admin.get_settings_bundle(conn, slug)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Restaurant not found; run the seed.")
    assistant = data["assistant"]
    if assistant["assistant_name"] is None:
        assistant = {"assistant_name": "Sara", "personality": "warm, professional and concise"}
    return SettingsOut(**{**data, "assistant": assistant})


@router.get("/settings", response_model=SettingsOut)
async def get_settings_view(_admin: AdminDep, db: DbDep, conn: ConnDep) -> SettingsOut:
    return await _bundle(conn, db.restaurant_slug)


@router.put("/settings/restaurant", response_model=SettingsOut)
async def put_restaurant(
    body: RestaurantIn, _admin: AdminDep, db: DbDep, conn: ConnDep
) -> SettingsOut:
    await restaurant_admin.update_restaurant(conn, db.restaurant_slug, body.model_dump())
    return await _bundle(conn, db.restaurant_slug)


@router.put("/settings/hours", response_model=SettingsOut)
async def put_hours(body: HoursIn, _admin: AdminDep, db: DbDep, conn: ConnDep) -> SettingsOut:
    hours = [(d.opens, d.closes) if d else None for d in body.days]
    await restaurant_admin.replace_hours(conn, db.restaurant_slug, hours)
    return await _bundle(conn, db.restaurant_slug)


@router.put("/settings/assistant", response_model=SettingsOut)
async def put_assistant(
    body: AssistantIn, _admin: AdminDep, db: DbDep, conn: ConnDep
) -> SettingsOut:
    await restaurant_admin.update_assistant(conn, db.restaurant_slug, body.model_dump())
    return await _bundle(conn, db.restaurant_slug)


@router.post("/settings/tables", response_model=TableOut, status_code=status.HTTP_201_CREATED)
async def post_table(body: TableIn, _admin: AdminDep, db: DbDep, conn: ConnDep) -> TableOut:
    return TableOut(**await restaurant_admin.add_table(conn, db.restaurant_slug, body.model_dump()))


@router.patch("/settings/tables/{table_id}", response_model=TableOut)
async def patch_table(
    table_id: UUID, body: TableUpdate, _admin: AdminDep, db: DbDep, conn: ConnDep
) -> TableOut:
    values = body.model_dump(exclude_none=True)
    if not values:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Nothing to change.")
    updated = await restaurant_admin.update_table(conn, db.restaurant_slug, str(table_id), values)
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such table.")
    return TableOut(**updated)
