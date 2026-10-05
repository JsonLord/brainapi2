"""Hosted workspace facade composed from the existing brain-scoped routers."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import os

from fastapi import APIRouter, Depends, Request
from fastapi.openapi.utils import get_openapi

from src.constants.data import BRAIN_VERSION
from src.services.api.constants.responses import StringListResponse
from src.services.api.controllers.meta import (
    get_entities_labels as get_entities_labels_controller,
    get_entity_properties as get_entity_properties_controller,
    get_relationships_properties as get_relationships_properties_controller,
)
from src.services.api.dependencies import get_brain_id
from src.services.api.routes.ingest import ingest_router
from src.services.api.routes.model import model_router
from src.services.api.routes.retrieve import retrieve_router
from src.services.api.routes.tasks import tasks_router
from src.services.data.main import data_adapter
from src.services.workspaces import public_base_url, workspace_payload

WORKSPACE_API_PREFIX = "/brains/{workspace_slug}/api"

workspace_api_router = APIRouter(
    prefix=WORKSPACE_API_PREFIX,
    tags=["workspace-api"],
)

# login-info is deliberately excluded: it describes the credential, not a brain.
workspace_meta_router = APIRouter(prefix="/meta", tags=["meta"])


@workspace_meta_router.get(
    "/relationships-properties", response_model=StringListResponse
)
async def workspace_relationship_properties(
    brain_id: str = Depends(get_brain_id),
):
    return await get_relationships_properties_controller(brain_id)


@workspace_meta_router.get("/entity-labels", response_model=StringListResponse)
async def workspace_entity_labels(brain_id: str = Depends(get_brain_id)):
    return await get_entities_labels_controller(brain_id)


@workspace_meta_router.get("/entity-properties", response_model=StringListResponse)
async def workspace_entity_properties(brain_id: str = Depends(get_brain_id)):
    return await get_entity_properties_controller(brain_id)


def _request_public_base(request: Request) -> str:
    return public_base_url(os.getenv("PUBLIC_BASE_URL"), str(request.base_url))


def _active_workspace(request: Request):
    # BrainMiddleware has already resolved, validated, and authorized this slug.
    return data_adapter.get_workspace(request.state.workspace_slug)


@workspace_api_router.get("", include_in_schema=False)
async def workspace_api_discovery(request: Request):
    workspace = _active_workspace(request)
    payload = workspace_payload(workspace, _request_public_base(request))
    return {
        "workspace": {
            "slug": workspace.slug,
            "display_name": workspace.display_name,
            "brain_id": workspace.brain_id,
        },
        "api_base_url": payload["api_base_url"],
        "status": "active",
        "links": {
            "native_api": payload["api_base_url"],
            "native_openapi": f'{payload["api_base_url"]}/openapi.json',
            # Retained for clients of the initial discovery contract.
            "openapi": f'{payload["api_base_url"]}/openapi.json',
            "console": payload["console_url"],
            "agent_api": payload["api_base_url"].removesuffix("/api") + "/agent",
            "agent_openapi": payload["api_base_url"].removesuffix("/api")
            + "/agent/openapi.json",
            "mcp": payload["api_base_url"].removesuffix("/api") + "/mcp",
        },
    }


def _schema_router() -> APIRouter:
    router = APIRouter()
    for source in (
        ingest_router,
        retrieve_router,
        model_router,
        tasks_router,
        workspace_meta_router,
    ):
        router.include_router(source)
    return router


@lru_cache(maxsize=1)
def _workspace_openapi_template() -> dict:
    schema = get_openapi(
        title="BrainAPI Workspace API",
        version=BRAIN_VERSION,
        description=(
            "Brain-scoped BrainAPI endpoints. The workspace URL is authoritative; "
            "X-Brain-ID is not required."
        ),
        routes=_schema_router().routes,
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {}).update(
        {
            "BrainPAT": {"type": "apiKey", "in": "header", "name": "BrainPAT"},
            "BearerAuth": {"type": "http", "scheme": "bearer"},
        }
    )
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict) and "responses" in operation:
                operation["security"] = [{"BrainPAT": []}, {"BearerAuth": []}]
    return schema


@workspace_api_router.get("/openapi.json", include_in_schema=False)
async def workspace_api_openapi(request: Request):
    schema = deepcopy(_workspace_openapi_template())
    workspace = _active_workspace(request)
    payload = workspace_payload(workspace, _request_public_base(request))
    schema["servers"] = [{"url": payload["api_base_url"]}]
    schema["info"]["title"] = f"{workspace.display_name} — BrainAPI Workspace API"
    return schema


def _include_hosted_router(router: APIRouter) -> None:
    """Include reused handlers while keeping global route names unambiguous."""
    start = len(workspace_api_router.routes)
    workspace_api_router.include_router(router, include_in_schema=False)
    for route in workspace_api_router.routes[start:]:
        methods = "_".join(sorted(getattr(route, "methods", ()) or ("route",)))
        suffix = route.path.removeprefix(WORKSPACE_API_PREFIX).strip("/")
        suffix = suffix.replace("/", "_").replace("{", "").replace("}", "")
        route.name = f"workspace_{route.name}_{methods.lower()}_{suffix or 'root'}"


# Reuse the exact legacy route handlers and dependencies. The encompassing
# workspace prefix is resolved by BrainMiddleware before these handlers run.
for _router in (
    ingest_router,
    retrieve_router,
    model_router,
    tasks_router,
    workspace_meta_router,
):
    _include_hosted_router(_router)
