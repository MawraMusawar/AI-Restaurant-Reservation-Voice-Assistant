"""Dashboard: headline numbers, 7-day charts, recent calls and reservation activity."""

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.auth import AdminDep
from app.api.deps import ConnDep, ProfileDep
from app.api.schemas import ReservationOut
from app.db.reports import dashboard

router = APIRouter(tags=["dashboard"])


class DashboardStats(BaseModel):
    total_reservations: int  # not cancelled, all time
    cancelled_reservations: int
    today_reservations: int
    today_guests: int
    upcoming_reservations: int
    total_calls: int
    successful_bookings: int  # calls that produced a (not cancelled) booking
    booking_rate: float | None
    avg_call_seconds: float | None


class DayBookings(BaseModel):
    date: str
    weekday: str
    bookings: int
    guests: int


class DayCalls(BaseModel):
    date: str
    weekday: str
    calls: int


class RecentCall(BaseModel):
    id: str
    started_at: datetime
    status: str
    end_reason: str | None
    error: str | None
    duration_seconds: float | None
    first_request: str | None
    booking_reference: str | None


class DashboardOut(BaseModel):
    restaurant_name: str
    today: str
    stats: DashboardStats
    bookings_next_7_days: list[DayBookings]
    calls_last_7_days: list[DayCalls]
    recent_calls: list[RecentCall]
    recent_activity: list[ReservationOut]


@router.get("/dashboard", response_model=DashboardOut)
async def get_dashboard(_admin: AdminDep, conn: ConnDep, profile: ProfileDep) -> DashboardOut:
    data = await dashboard(conn, profile, profile.now())
    return DashboardOut(restaurant_name=profile.name, **data)
