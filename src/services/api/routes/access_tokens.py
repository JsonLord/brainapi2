"""Administrator-only explicit credential issuance and revocation."""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, AwareDatetime

from src.services.api.constants.auth import AccessTokenListResponse, IssuedAccessTokenResponse, AccessTokenMetadataResponse
from src.services.auth import AuthError, get_auth_service
from src.services.brain_identity import validate_brain_id
from src.services.credentials import token_repository

access_token_router = APIRouter(prefix="/system/access-tokens", tags=["access-tokens"])


class TokenCreation(BaseModel):
    brain_id: str
    label: str = Field(min_length=1, max_length=100)
    permissions: list[str] = Field(min_length=1, max_length=4)
    expires_at: AwareDatetime | None = None


@access_token_router.get("", response_model=AccessTokenListResponse)
async def list_tokens(request: Request, response: Response, brain_id: str | None = None):
    response.headers["Cache-Control"] = "no-store"
    if brain_id is not None:
        brain_id = validate_brain_id(brain_id)
    return {"tokens": get_auth_service().list_access_tokens(request.state.principal, brain_id)}


@access_token_router.post("", status_code=201, response_model=IssuedAccessTokenResponse)
async def create_token(body: TokenCreation, request: Request, response: Response):
    brain_id = validate_brain_id(body.brain_id)
    service = get_auth_service()
    try:
        issued = service.create_access_token(request.state.principal, brain_id=brain_id,
            label=body.label, permissions=body.permissions, expires_at=body.expires_at)
    except ValueError:
        raise HTTPException(422, "Invalid permissions or expiry") from None
    response.headers["Cache-Control"] = "no-store"
    return issued


@access_token_router.delete("/{token_id}", response_model=AccessTokenMetadataResponse)
async def revoke_token(token_id: str, request: Request):
    try:
        return get_auth_service().revoke_access_token(request.state.principal, token_id)
    except AuthError as error:
        raise HTTPException(error.status, error.code) from None
