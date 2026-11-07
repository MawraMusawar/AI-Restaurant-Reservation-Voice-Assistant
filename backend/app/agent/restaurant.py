"""The restaurant the assistant answers for.

Loaded per call from the database (`app.db.restaurants`): the `restaurants`,
`restaurant_hours` and `assistant_settings` tables. `DEMO_RESTAURANT` mirrors
`supabase/seed.sql`; it is used by tests and when no database is configured.
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class OpeningHours(BaseModel):
    opens: time
    closes: time

    @model_validator(mode="after")
    def _closes_after_opens(self) -> "OpeningHours":
        # Hours that run past midnight are not supported yet.
        if self.closes <= self.opens:
            raise ValueError("closes must be after opens")
        return self


class ReservationRules(BaseModel):
    min_guests: int = Field(default=1, ge=1)
    max_guests: int = Field(default=10, ge=1)
    # How far ahead a table can be booked.
    booking_window_days: int = Field(default=30, ge=1)
    # Bookings must start at least this long before closing.
    last_seating_minutes_before_close: int = Field(default=60, ge=0)
    # Bookings must start at least this far in the future.
    min_notice_minutes: int = Field(default=30, ge=0)
    # How long a booking holds its table, and the step between bookable times.
    dining_duration_minutes: int = Field(default=90, ge=15)
    slot_interval_minutes: int = Field(default=15, ge=5)
    require_phone: bool = False


class RestaurantProfile(BaseModel):
    # Database id; None for the in-code demo profile.
    id: str | None = None
    booking_reference_prefix: str = "RS"
    name: str
    assistant_name: str
    cuisine: str
    description: str
    address: str
    phone: str
    email: str
    timezone: str
    # Index 0 = Monday. None = closed all day.
    hours: list[OpeningHours | None] = Field(min_length=7, max_length=7)
    services: list[str] = Field(default_factory=list)
    policies: list[str] = Field(default_factory=list)
    rules: ReservationRules = Field(default_factory=ReservationRules)
    # Assistant settings
    personality: str = "warm, professional and concise"
    greeting: str | None = None  # None = built from the restaurant name
    voice_id: str | None = None  # None = ELEVENLABS_VOICE_ID / default voice

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def hours_for(self, weekday: int) -> OpeningHours | None:
        return self.hours[weekday]

    def hours_summary(self) -> list[str]:
        """One line per weekday, e.g. 'Tuesday: 12:00-22:00'."""
        lines = []
        for index, day in enumerate(WEEKDAYS):
            slot = self.hours[index]
            when = "closed" if slot is None else f"{slot.opens:%H:%M}-{slot.closes:%H:%M}"
            lines.append(f"{day}: {when}")
        return lines


_LUNCH_DINNER = OpeningHours(opens=time(12, 0), closes=time(22, 30))
_WEEKEND = OpeningHours(opens=time(12, 0), closes=time(23, 30))

# Fictional restaurant used for the client demo.
DEMO_RESTAURANT = RestaurantProfile(
    booking_reference_prefix="OE",
    name="Olive & Ember",
    assistant_name="Sara",
    cuisine="Mediterranean grill",
    description="A relaxed Mediterranean grill with wood-fired dishes and a garden terrace.",
    address="14 Garden Avenue, Lahore",
    phone="+92 42 0000 0000",
    email="reservations@olive-and-ember.example",
    timezone="Asia/Karachi",
    hours=[
        None,  # Monday
        _LUNCH_DINNER,
        _LUNCH_DINNER,
        _LUNCH_DINNER,
        _WEEKEND,  # Friday
        _WEEKEND,  # Saturday
        OpeningHours(opens=time(12, 0), closes=time(21, 30)),  # Sunday
    ],
    services=[
        "Dine-in with indoor and garden terrace seating",
        "Takeaway",
        "Private dining room for up to 20 guests (arranged by email)",
        "Vegetarian, vegan and gluten-free options",
        "Free parking",
    ],
    policies=[
        "Tables are held for 15 minutes after the booking time.",
        "Parties larger than 10 are arranged by email.",
        "Children are welcome; high chairs are available.",
    ],
)
