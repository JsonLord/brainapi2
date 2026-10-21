"""Administrator-only explicit credential issuance and revocation."""

from datetime import datetime
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, AwareDatetime

from src.services.auth import AuthError, get_auth_service
from src.services.brain_identity import validate_brain_id
from src.services.credentials import PERMISSIONS, token_repository

access_token_router = APIRouter(prefix="/system/access-tokens", tags=["access-tokens"])


class TokenCreation(BaseModel):
    brain_id: str
    label: str = Field(min_length=1, max_length=100)
    permissions: list[str] = Field(min_length=1, max_length=4)
    expires_at: AwareDatetime | None = None


@access_token_router.get("")
async def list_tokens(response: Response, brain_id: str | None = None):
    response.headers["Cache-Control"] = "no-store"
    if brain_id is not None:
        brain_id = validate_brain_id(brain_id)
    return {"tokens": token_repository.list(brain_id)}


@access_token_router.post("", status_code=201)
async def create_token(body: TokenCreation, request: Request, response: Response):
    brain_id = validate_brain_id(body.brain_id)
    service = get_auth_service()
    brain = service.data.get_brain(brain_id)
    if brain is None:
        raise HTTPException(404, "BRAIN_NOT_FOUND")
    try:
        issued = token_repository.create(brain=brain, label=body.label.strip(), permissions=body.permissions,
                                         expires_at=body.expires_at, created_by=request.state.principal.subject_id)
    except ValueError:
        raise HTTPException(422, "Invalid permissions or expiry") from None
    response.headers["Cache-Control"] = "no-store"
    return issued


@access_token_router.delete("/{token_id}")
async def revoke_token(token_id: str):
    try:
        return token_repository.revoke(token_id)
    except AuthError as error:
        raise HTTPException(error.status, error.code) from None
