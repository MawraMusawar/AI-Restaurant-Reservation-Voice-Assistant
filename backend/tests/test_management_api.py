"""Management API: admin auth, dashboard, reservations, settings, call logs.

Auth is tested without a database. The rest runs against the real database
(DATABASE_URL), with the app pointed at a throwaway restaurant (see conftest.py),
so the demo data is never touched.
"""

import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from app.api.auth import COOKIE_NAME, issue_token, token_is_valid
from app.config import Settings, get_settings
from app.db import Database
from app.db.sessions import save_session
from app.main import create_app
from app.voice.sessions import VoiceSession

PASSWORD = "test-admin-pass"
needs_db = pytest.mark.skipif(get_settings().database_url is None, reason="DATABASE_URL not set")


def admin_settings() -> Settings:
    return get_settings().model_copy(update={"admin_password": SecretStr(PASSWORD)})


@pytest.fixture
async def app_client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_settings] = admin_settings
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


async def sign_in(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/auth/login", json={"password": PASSWORD})
    assert response.status_code == 200, response.text


# --- Auth ------------------------------------------------------------------------------


def test_tokens_expire_and_cannot_be_forged() -> None:
    settings = admin_settings()
    token = issue_token(settings)
    assert token_is_valid(settings, token)
    assert not token_is_valid(settings, token, now=time.time() + 13 * 3600)  # 12 h sessions
    payload, signature = token.split(".")
    assert not token_is_valid(settings, payload + "." + signature[::-1])
    other = settings.model_copy(update={"admin_password": SecretStr("changed")})
    assert not token_is_valid(other, token)  # a new password signs everyone out


async def test_management_endpoints_require_login(app_client: httpx.AsyncClient) -> None:
    for path in ("/api/dashboard", "/api/reservations", "/api/settings", "/api/calls"):
        assert (await app_client.get(path)).status_code == 401, path
    wrong = await app_client.post("/api/auth/login", json={"password": "nope"})
    assert wrong.status_code == 401
    await sign_in(app_client)
    assert app_client.cookies.get(COOKIE_NAME)
    assert (await app_client.get("/api/auth/me")).json() == {"signed_in": True, "configured": True}
    await app_client.post("/api/auth/logout")
    assert (await app_client.get("/api/auth/me")).json()["signed_in"] is False


async def test_locked_when_no_admin_password_is_configured(app_client: httpx.AsyncClient):
    app_client.app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(  # type: ignore[attr-defined]
        update={"admin_password": None}
    )
    login = await app_client.post("/api/auth/login", json={"password": PASSWORD})
    assert login.status_code == 503 and "ADMIN_PASSWORD" in login.json()["detail"]
    me = (await app_client.get("/api/auth/me")).json()
    assert me == {"signed_in": False, "configured": False}
    assert (await app_client.get("/api/dashboard")).status_code == 503


async def test_management_reports_a_missing_database(app_client: httpx.AsyncClient) -> None:
    await sign_in(app_client)
    app_client.app.state.db = None  # type: ignore[attr-defined]
    response = await app_client.get("/api/dashboard")
    assert response.status_code == 503 and "not connected" in response.json()["detail"]


# --- With the database -----------------------------------------------------------------


@pytest.fixture
async def admin(app_client: httpx.AsyncClient, restaurant: str) -> AsyncIterator[httpx.AsyncClient]:
    """A signed-in client whose app serves the throwaway restaurant."""
    database: Database = app_client.app.state.db  # type: ignore[attr-defined]
    database.restaurant_slug = restaurant
    await sign_in(app_client)
    yield app_client


def future_day(days: int = 3) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).date().isoformat()


@needs_db
@pytest.mark.integration
async def test_staff_create_list_and_cancel_reservations(admin: httpx.AsyncClient) -> None:
    day = future_day()
    body = {"customer_name": "  Hina   Khan ", "phone": "0300 123 4567", "date": day,
            "time": "19:00", "guests": 2}  # fmt: skip
    created = await admin.post("/api/reservations", json=body)
    assert created.status_code == 201, created.text
    booking = created.json()
    assert booking["customer_name"] == "Hina Khan" and booking["phone"] == "03001234567"
    assert (booking["status"], booking["source"], booking["table"]) == ("confirmed", "admin", "T2")
    reference = booking["booking_reference"]

    listed = (await admin.get("/api/reservations", params={"from": day, "to": day})).json()
    assert [r["booking_reference"] for r in listed] == [reference]
    found = (await admin.get("/api/reservations", params={"q": "hina"})).json()
    assert found and found[0]["booking_reference"] == reference

    # Fill the other table, then the slot is full: 409 with alternatives.
    second = await admin.post(
        "/api/reservations", json={**body, "customer_name": "Two", "phone": "03007654321"}
    )
    assert second.status_code == 201 and second.json()["table"] == "T4"
    full = await admin.post(
        "/api/reservations", json={**body, "customer_name": "Three", "phone": None}
    )
    assert full.status_code == 409, full.text
    assert full.json()["detail"]["code"] == "slot_unavailable"
    assert full.json()["detail"]["alternatives"]

    cancelled = await admin.post(f"/api/reservations/{reference}/cancel")
    assert cancelled.json() == {"booking_reference": reference, "status": "cancelled"}
    again = await admin.post(f"/api/reservations/{reference}/cancel")
    assert again.status_code == 409
    missing = await admin.post("/api/reservations/TS-ZZZZZZ/cancel")
    assert missing.status_code == 404
    only_cancelled = await admin.get("/api/reservations", params={"status": "cancelled"})
    assert [r["booking_reference"] for r in only_cancelled.json()] == [reference]


@needs_db
@pytest.mark.integration
async def test_staff_bookings_follow_the_same_rules(admin: httpx.AsyncClient) -> None:
    base = {"customer_name": "Ali", "date": future_day(), "time": "19:00", "guests": 2}
    past = await admin.post("/api/reservations", json={**base, "date": "2020-01-01"})
    assert past.status_code == 422 and past.json()["detail"]["code"] == "date_in_past"
    late = await admin.post("/api/reservations", json={**base, "time": "23:30"})
    assert late.status_code == 422 and late.json()["detail"]["code"] == "outside_opening_hours"
    bad_phone = await admin.post("/api/reservations", json={**base, "phone": "12"})
    assert bad_phone.status_code == 422
    too_many = await admin.post("/api/reservations", json={**base, "guests": 11})
    assert too_many.json()["detail"]["code"] == "party_too_large"


@needs_db
@pytest.mark.integration
async def test_dashboard_counts_and_charts(admin: httpx.AsyncClient) -> None:
    day = future_day(2)
    await admin.post(
        "/api/reservations",
        json={"customer_name": "Dash", "date": day, "time": "13:00", "guests": 3},
    )
    data = (await admin.get("/api/dashboard")).json()
    assert data["restaurant_name"] == "Test Bistro"
    assert data["stats"]["total_reservations"] == 1
    assert data["stats"]["upcoming_reservations"] == 1
    assert len(data["bookings_next_7_days"]) == 7 and len(data["calls_last_7_days"]) == 7
    chart_day = next(d for d in data["bookings_next_7_days"] if d["date"] == day)
    assert (chart_day["bookings"], chart_day["guests"]) == (1, 3)
    assert data["recent_activity"][0]["customer_name"] == "Dash"


@needs_db
@pytest.mark.integration
async def test_settings_round_trip(admin: httpx.AsyncClient) -> None:
    settings = (await admin.get("/api/settings")).json()
    assert settings["restaurant"]["name"] == "Test Bistro"
    assert len(settings["hours"]) == 7 and len(settings["tables"]) == 2

    hours = [{"opens": "12:00", "closes": "22:00"}] * 7
    hours[0] = None  # closed Mondays
    updated = (await admin.put("/api/settings/hours", json={"days": hours})).json()
    assert updated["hours"][0] is None and updated["hours"][1]["closes"] == "22:00:00"
    bad_hours = await admin.put(
        "/api/settings/hours", json={"days": [{"opens": "22:00", "closes": "12:00"}] * 7}
    )
    assert bad_hours.status_code == 422

    restaurant = {**settings["restaurant"], "name": "Test Bistro 2", "max_party_size": 8}
    restaurant.pop("id"), restaurant.pop("slug")
    put = await admin.put("/api/settings/restaurant", json=restaurant)
    assert put.json()["restaurant"]["name"] == "Test Bistro 2"
    bad_tz = await admin.put("/api/settings/restaurant", json={**restaurant, "timezone": "Mars"})
    assert bad_tz.status_code == 422

    table = await admin.post("/api/settings/tables", json={"label": "T9", "seats": 6})
    assert table.status_code == 201
    duplicate = await admin.post("/api/settings/tables", json={"label": "T9", "seats": 6})
    assert duplicate.status_code == 409
    off = await admin.patch(f"/api/settings/tables/{table.json()['id']}", json={"is_active": False})
    assert off.json()["is_active"] is False

    assistant = await admin.put(
        "/api/settings/assistant",
        json={"assistant_name": "Maya", "personality": "cheerful", "greeting": " "},
    )
    assert assistant.json()["assistant"] == {
        "assistant_name": "Maya", "greeting": None, "personality": "cheerful", "voice_id": None,
    }  # fmt: skip


@needs_db
@pytest.mark.integration
async def test_call_logs_list_and_detail(admin: httpx.AsyncClient, restaurant: str) -> None:
    database: Database = admin.app.state.db  # type: ignore[attr-defined]
    async with database.pool.acquire() as conn:
        rid = str(await conn.fetchval("select id from restaurants where slug = $1", restaurant))
    session = VoiceSession(session_id=f"test-call-{restaurant}")
    session.mark_active()
    session.add_transcript("assistant", "Thank you for calling.")
    session.add_transcript("user", "Do you have a table tonight?")
    session.status, session.end_reason, session.ended_at = (
        "ended",
        "assistant_ended",
        datetime.now(UTC),
    )
    await save_session(database.pool, session, rid)
    try:
        calls = (await admin.get("/api/calls")).json()
        mine = next(c for c in calls if c["id"] == session.session_id)
        assert mine["first_request"] == "Do you have a table tonight?"
        assert (mine["message_count"], mine["live"]) == (2, False)
        detail = (await admin.get(f"/api/calls/{session.session_id}")).json()
        assert [m["role"] for m in detail["transcript"]] == ["assistant", "user"]
        assert detail["end_reason"] == "assistant_ended"
        assert (await admin.get("/api/calls/does-not-exist")).status_code == 404
    finally:
        async with database.pool.acquire() as conn:
            await conn.execute("delete from voice_sessions where id = $1", session.session_id)


@needs_db
@pytest.mark.integration
async def test_double_submitted_staff_booking_is_not_duplicated(admin: httpx.AsyncClient) -> None:
    body = {"customer_name": "Hina", "phone": "03001234567", "date": future_day(),
            "time": "20:00", "guests": 2}  # fmt: skip
    first = await admin.post("/api/reservations", json=body)
    again = await admin.post("/api/reservations", json=body)
    assert first.status_code == 201
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_booked"
    assert first.json()["booking_reference"] in again.json()["detail"]["detail"]
