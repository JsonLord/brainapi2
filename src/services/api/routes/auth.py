"""TLS console login; browsers receive an opaque cookie, never the input token."""

import os
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, SecretStr, Field

from src.services.auth import AuthError, get_auth_service
from src.services.console_sessions import SESSION_COOKIE, get_console_sessions

auth_router = APIRouter(prefix="/auth", tags=["auth"])


class SessionLogin(BaseModel):
    token: SecretStr = Field(min_length=1, max_length=4096)


def check_origin(request):
    origin = request.headers.get("origin")
    allowed = (os.getenv("PUBLIC_BASE_URL") or str(request.base_url)).rstrip("/")
    if origin and origin != allowed:
        raise HTTPException(403, "CSRF_ORIGIN_INVALID")


@auth_router.post("/session")
async def login(body: SessionLogin, request: Request, response: Response):
    check_origin(request)
    if request.url.scheme != "https" and os.getenv("ENV", "production") != "development":
        raise HTTPException(400, "HTTPS_REQUIRED")
    try:
        principal = get_auth_service().authenticate_token(body.token.get_secret_value())
    except AuthError as error:
        raise HTTPException(error.status, error.code) from None
    sessions = get_console_sessions()
    # Discard a prior session on login to prevent session fixation/leaks.
    sessions.revoke(request.cookies.get(SESSION_COOKIE))
    cookie, csrf, ttl = sessions.create(principal)
    response.set_cookie(SESSION_COOKIE, cookie, max_age=ttl, httponly=True,
                        secure=True, samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return {**principal.public(), "csrf_token": csrf}


@auth_router.get("/me")
async def me(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {**request.state.principal.public(), "csrf_token": getattr(request.state, "csrf_token", None)}


@auth_router.post("/logout")
async def logout(request: Request, response: Response):
    get_console_sessions().revoke(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": False}
