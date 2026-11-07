"""Response models shared by the management API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ReservationStatus = Literal["confirmed", "cancelled", "completed", "no_show"]


class ReservationOut(BaseModel):
    id: str
    booking_reference: str
    customer_name: str
    phone: str | None
    guests: int
    date: str  # restaurant-local date, YYYY-MM-DD
    weekday: str
    time: str  # restaurant-local time, HH:MM
    table: str
    area: str
    status: ReservationStatus
    source: Literal["voice", "web", "admin"]
    special_requests: str | None
    voice_session_id: str | None
    created_at: datetime
    cancelled_at: datetime | None
