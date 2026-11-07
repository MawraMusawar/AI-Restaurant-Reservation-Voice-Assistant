"""Reservation management: list, create (staff), cancel.

Staff bookings use the same validation (`booking_rules`) and the same engine
(`PostgresReservationBackend`) as the voice assistant, so the double-booking guards
and suggested alternatives are identical.
"""

import re
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field, field_validator

from app.agent.booking_rules import RuleViolation, validate_booking_request
from app.agent.reservations import NewReservation
from app.api.auth import AdminDep
from app.api.deps import ConnDep, DbDep, ProfileDep
from app.api.schemas import ReservationOut, ReservationStatus
from app.db.reports import RESERVATION_SELECT, list_reservations, reservation_view
from app.db.reservations import PostgresReservationBackend

router = APIRouter(tags=["reservations"])

PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")


class ReservationCreate(BaseModel):
    customer_name: str = Field(min_length=1, max_length=80)
    phone: str | None = Field(default=None, max_length=30)
    date: date
    time: str = Field(pattern=r"^\d{2}:\d{2}$")
    guests: int = Field(ge=1, le=30)
    special_requests: str | None = Field(default=None, max_length=300)

    @field_validator("customer_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("name is required")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        cleaned = ("+" if v.strip().startswith("+") else "") + re.sub(r"\D", "", v)
        if not PHONE_RE.match(cleaned):
            raise ValueError("phone must have 7-15 digits")
        return cleaned

    @field_validator("special_requests")
    @classmethod
    def _requests(cls, v: str | None) -> str | None:
        return " ".join(v.split()) or None if v else None


class Alternative(BaseModel):
    date: str
    time: str


class BookingProblem(BaseModel):
    """Body of a 409/422: why it couldn't be booked, and what is free instead."""

    detail: str
    code: str
    alternatives: list[Alternative] = []


@router.get("/reservations", response_model=list[ReservationOut])
async def get_reservations(
    _admin: AdminDep,
    conn: ConnDep,
    profile: ProfileDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    status_filter: Annotated[ReservationStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[dict[str, Any]]:
    return await list_reservations(
        conn,
        profile,
        date_from=date_from,
        date_to=date_to,
        status=status_filter,
        query=q,
        limit=limit,
    )


def _problem(code: int, result: dict[str, Any]) -> HTTPException:
    return HTTPException(
        code,
        BookingProblem(
            detail=result.get("message", "Not possible."),
            code=result.get("error_code", "error"),
            alternatives=result.get("alternatives", []),
        ).model_dump(),
    )


@router.post(
    "/reservations",
    response_model=ReservationOut,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": BookingProblem}, 422: {"model": BookingProblem}},
)
async def create_reservation(
    body: ReservationCreate, _admin: AdminDep, db: DbDep, conn: ConnDep, profile: ProfileDep
) -> dict[str, Any]:
    slot = validate_booking_request(
        profile, body.date.isoformat(), body.time, body.guests, profile.now()
    )
    if isinstance(slot, RuleViolation):
        raise _problem(status.HTTP_422_UNPROCESSABLE_CONTENT, slot.as_result())
    if profile.rules.require_phone and body.phone is None:
        raise _problem(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"error_code": "missing_phone", "message": "This restaurant requires a phone number."},
        )
    backend = PostgresReservationBackend(db.pool, profile, source="admin")
    result = await backend.create_reservation(
        NewReservation(
            slot=slot,
            customer_name=body.customer_name,
            phone=body.phone,
            special_requests=body.special_requests,
        )
    )
    if result.get("error_code") == "database_error":
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, result["message"])
    if not result.get("ok"):
        # slot_unavailable (with alternatives) or already_booked (with the existing booking)
        if result.get("error_code") == "already_booked":
            reference = result["existing_booking"]["booking_reference"]
            result = {**result, "message": f"This customer already has this booking ({reference})."}
        raise _problem(status.HTTP_409_CONFLICT, result)
    row = await conn.fetchrow(
        RESERVATION_SELECT + " where r.booking_reference = $1", result["booking_reference"]
    )
    return reservation_view(profile, row)


CANCEL_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "not_active": status.HTTP_409_CONFLICT,
    "already_started": status.HTTP_409_CONFLICT,
    "database_error": status.HTTP_503_SERVICE_UNAVAILABLE,
}


class CancelResult(BaseModel):
    booking_reference: str
    status: Literal["cancelled"]


@router.post("/reservations/{booking_reference}/cancel", response_model=CancelResult)
async def cancel_reservation(
    booking_reference: Annotated[str, Path(pattern=r"^[A-Za-z]{2,4}-[A-Za-z0-9]{6}$")],
    _admin: AdminDep,
    db: DbDep,
    profile: ProfileDep,
) -> CancelResult:
    backend = PostgresReservationBackend(db.pool, profile, source="admin")
    result = await backend.cancel_reservation(booking_reference.upper())
    if not result.get("ok"):
        raise HTTPException(
            CANCEL_STATUS.get(result.get("error_code", ""), status.HTTP_409_CONFLICT),
            result.get("message", "Could not cancel."),
        )
    return CancelResult(booking_reference=result["booking_reference"], status="cancelled")
