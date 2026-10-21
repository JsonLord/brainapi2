"""Credential-safe, explicit public contracts for authentication endpoints."""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class PrincipalResponse(BaseModel):
    authenticated: bool
    kind: Literal["system", "access_token"]
    is_system: bool
    brain_id: str | None
    permissions: list[str]
    legacy: bool = False


class SessionResponse(PrincipalResponse):
    csrf_token: str | None = Field(default=None, description="CSRF proof for unsafe cookie-authenticated requests; never an access credential.")


class LogoutResponse(BaseModel):
    authenticated: Literal[False]


class AccessTokenMetadataResponse(BaseModel):
    id: str
    label: str
    brain_id: str
    token_prefix: str
    permissions: list[str]
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    legacy: bool
    created_by: str


class IssuedAccessTokenResponse(AccessTokenMetadataResponse):
    token: str = Field(repr=False, description="Shown exactly once at creation. Store securely; listing never reveals this value.")


class AccessTokenListResponse(BaseModel):
    tokens: list[AccessTokenMetadataResponse]
