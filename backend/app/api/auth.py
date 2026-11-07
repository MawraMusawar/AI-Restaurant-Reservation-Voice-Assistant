"""Admin login for the management screens (dashboard, reservations, settings, calls).

One admin password (`ADMIN_PASSWORD` in backend/.env) protects them. A successful
login sets an HttpOnly, SameSite=Lax cookie holding an expiring token signed with
HMAC-SHA256. The browser only ever talks to the Next.js origin, which proxies
`/api/*`, so the cookie is first-party. The live call screen stays public: it is
the caller's side.
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from loguru import logger
from pydantic import BaseModel, Field

from app.config import Settings, get_settings

router = APIRouter(tags=["auth"])

COOKIE_NAME = "admin_session"
SESSION_SECS = 12 * 60 * 60
NOT_CONFIGURED = "Admin login is not configured. Set ADMIN_PASSWORD in backend/.env."

SettingsDep = Annotated[Settings, Depends(get_settings)]


def _secret(settings: Settings) -> bytes:
    """Signing key: ADMIN_SESSION_SECRET, or derived from the password (so changing the
    password signs everyone out)."""
    if settings.admin_session_secret is not None:
        return settings.admin_session_secret.get_secret_value().encode()
    assert settings.admin_password is not None
    password = settings.admin_password.get_secret_value()
    return hashlib.sha256(f"admin-session:{password}".encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_token(settings: Settings, now: float | None = None) -> str:
    payload = _b64(json.dumps({"exp": int((now or time.time()) + SESSION_SECS)}).encode())
    signature = _b64(hmac.new(_secret(settings), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def token_is_valid(settings: Settings, token: str, now: float | None = None) -> bool:
    try:
        payload, signature = token.split(".", 1)
        expected = _b64(hmac.new(_secret(settings), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return False
        return json.loads(_unb64(payload))["exp"] > (now or time.time())
    except (ValueError, KeyError, TypeError):
        return False


def require_admin(request: Request, settings: SettingsDep) -> None:
    """Dependency for every management endpoint."""
    if settings.admin_password is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONFIGURED)
    token = request.cookies.get(COOKIE_NAME)
    if not token or not token_is_valid(settings, token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in.")


AdminDep = Annotated[None, Depends(require_admin)]


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class AuthStatus(BaseModel):
    signed_in: bool
    # False = ADMIN_PASSWORD isn't set, so nobody can sign in yet.
    configured: bool = True


@router.post("/auth/login", response_model=AuthStatus)
async def login(body: LoginRequest, response: Response, settings: SettingsDep) -> AuthStatus:
    if settings.admin_password is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONFIGURED)
    if not hmac.compare_digest(
        body.password.encode(), settings.admin_password.get_secret_value().encode()
    ):
        logger.warning("Failed admin login")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong password.")
    response.set_cookie(
        COOKIE_NAME,
        issue_token(settings),
        max_age=SESSION_SECS,
        httponly=True,
        samesite="lax",
        secure=settings.app_env == "production",
        path="/",
    )
    return AuthStatus(signed_in=True)


@router.post("/auth/logout", response_model=AuthStatus)
async def logout(response: Response) -> AuthStatus:
    response.delete_cookie(COOKIE_NAME, path="/")
    return AuthStatus(signed_in=False)


@router.get("/auth/me", response_model=AuthStatus)
async def me(request: Request, settings: SettingsDep) -> AuthStatus:
    """Whether this browser is signed in. Never an error, so pages can check it quietly."""
    if settings.admin_password is None:
        return AuthStatus(signed_in=False, configured=False)
    token = request.cookies.get(COOKIE_NAME)
    return AuthStatus(signed_in=bool(token and token_is_valid(settings, token)))
