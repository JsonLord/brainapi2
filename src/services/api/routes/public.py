"""Public, read-only API surfaces intended for discovery and evaluation."""

from __future__ import annotations

import os

from src.services.brain_identity import InvalidBrainIdError, validate_brain_id

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.config import config
from src.constants.data import BRAIN_VERSION
from src.services.api.constants.requests import SearchRequestBody, SearchResponse
from src.services.api.controllers.search import search as search_controller
from src.services.api.errors import ErrorResponse


class HealthResponse(BaseModel):
    status: str = Field(description="Service health status.")
    service: str = Field(description="Service name.")
    version: str = Field(description="Running BrainAPI version.")
    backup: dict = Field(default_factory=dict, description="Non-secret remote checkpoint state.")
    persistence: dict = Field(default_factory=dict, description="Non-secret durability and release status.")
    storage: dict = Field(default_factory=dict, description="Local storage and explicit checkpoint RPO policy.")
    checks: dict = Field(default_factory=dict, description="Safe dependency readiness results.")
    maintenance: dict = Field(default_factory=dict, description="Write admission and maintenance hold state.")
    restored_checkpoint: str | None = None
    remote_checkpoint: str | None = None
    checkpoint_lag: int | None = None
    backup_dirty: bool = False
    backup_last_success: str | None = None
    backup_age_seconds: float | None = None
    content_durable: bool = False
    zero_loss_durable: bool = False
    rpo_seconds: int | None = None


PUBLIC_ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Public demo is disabled"},
    422: {"model": ErrorResponse, "description": "Invalid query"},
    500: {"model": ErrorResponse, "description": "Internal service error"},
    503: {"model": ErrorResponse, "description": "Demo search is unavailable"},
}

public_router = APIRouter(tags=["public"])


@public_router.get(
    "/health",
    response_model=HealthResponse,
    openapi_extra={"security": []},
)
async def health() -> HealthResponse:
    """Return public liveness metadata without accessing customer data."""

    from src.services.persistence import persistence_state
    from src.services.brain_backup.state import public_status
    backup = public_status()
    from src.services.brain_backup.health import checks as probe
    import asyncio
    checks = await asyncio.to_thread(probe) if backup.get("enabled") else {}
    # Probing may detect a lineage conflict; refresh state before reporting it.
    backup = public_status()
    if checks:
        backup.update(remote_checkpoint=checks.get("remote_checkpoint"))
        remote = checks.get("remote_checkpoint")
        backup["checkpoint_lag"] = (0 if backup.get("commit") == remote else 1) if remote else None
    state = persistence_state(backup=backup, checks=checks)
    from src.services.brain_backup.maintenance import status as maintenance_status
    maintenance = maintenance_status()
    state['writable'] = not maintenance.get('quiesced', False)
    if maintenance.get('error'):
        state['production_ready'] = False
    fields = {key: backup.get(key) for key in ("restored_checkpoint", "remote_checkpoint", "checkpoint_lag",
              "backup_last_success", "backup_age_seconds")}
    return HealthResponse(status="ok" if state["production_ready"] else "degraded",
                          service="brainapi", version=BRAIN_VERSION, persistence=state, storage=state,
                          backup=backup, checks=checks, maintenance=maintenance, backup_dirty=backup.get("dirty", False),
                          content_durable=state["content_durable"], zero_loss_durable=False,
                          rpo_seconds=state["rpo_seconds"], **fields)


@public_router.get(
    "/demo/search",
    response_model=SearchResponse,
    responses=PUBLIC_ERROR_RESPONSES,
    openapi_extra={"security": []},
)
async def demo_search(
    query: str = Query(
        ...,
        min_length=1,
        max_length=500,
        description="Search query over the public BrainAPI documentation corpus.",
    ),
    k: int = Query(
        10,
        ge=1,
        le=10,
        description="Number of public documentation passages to return (maximum 10).",
    ),
) -> SearchResponse:
    """Search the fixed public demo brain using passages-only BM25 retrieval.

    This endpoint never accepts a caller-selected brain and exposes no write path.
    Production applies an additional per-client rate limit at the edge.
    """

    if os.getenv("PUBLIC_DEMO_ENABLED", "false").strip().lower() != "true":
        raise HTTPException(status_code=404, detail="Public demo search is disabled")

    brain_id = os.getenv("PUBLIC_DEMO_BRAIN_ID", "agentdemo").strip()
    try:
        brain_id = validate_brain_id(brain_id)
    except InvalidBrainIdError:
        raise HTTPException(
            status_code=503,
            detail="Public demo brain is not configured correctly",
        )

    try:
        configured_max = int(os.getenv("PUBLIC_DEMO_MAX_K", "10"))
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="PUBLIC_DEMO_MAX_K must be an integer",
        ) from exc
    max_k = min(max(configured_max, 1), 10)
    if k > max_k:
        raise HTTPException(
            status_code=422,
            detail=f"k must be less than or equal to {max_k}",
        )

    if (
        not config.search_enabled
        or not config.search_use_bm25
        or config.search_use_dense
    ):
        raise HTTPException(
            status_code=503,
            detail="Public demo requires BM25-only Search configuration",
        )

    return await search_controller(
        SearchRequestBody(
            query=query,
            k=k,
            channels=["passages"],
            brain_id=brain_id,
        )
    )
