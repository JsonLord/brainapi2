"""Compact, stable HTTP contract for external memory agents."""

from copy import deepcopy
from functools import lru_cache
import os

from fastapi import APIRouter, Depends, Request
from fastapi.openapi.utils import get_openapi

from src.constants.data import BRAIN_VERSION
from src.services.agent_memory import (
    memory_context,
    memory_search,
    memory_store,
    memory_task_status,
)
from src.services.api.constants.agent import (
    AgentContextRequest,
    AgentMemoryRequest,
    AgentSearchRequest,
)
from src.services.api.dependencies import get_brain_id
from src.services.data.main import data_adapter
from src.services.workspaces import public_base_url

AGENT_PREFIX = "/brains/{workspace_slug}/agent"
agent_router = APIRouter(prefix=AGENT_PREFIX, tags=["agent-memory"])


def _workspace(request: Request):
    return data_adapter.get_workspace(request.state.workspace_slug)


def _base(request: Request) -> str:
    root = public_base_url(os.getenv("PUBLIC_BASE_URL"), str(request.base_url))
    return f"{root}/brains/{request.state.workspace_slug}/agent"


@agent_router.post("/search")
async def agent_search(
    body: AgentSearchRequest,
    request: Request,
    brain_id: str = Depends(get_brain_id),
):
    return {
        "workspace": request.state.workspace_slug,
        "results": await memory_search(body.query, body.limit, brain_id),
    }


@agent_router.post("/context")
async def agent_context(
    body: AgentContextRequest,
    request: Request,
    brain_id: str = Depends(get_brain_id),
):
    result = await memory_context(body.query, body.max_tokens, brain_id)
    return {**result, "workspace": request.state.workspace_slug}


@agent_router.post("/memory", status_code=202)
async def agent_memory(
    body: AgentMemoryRequest,
    request: Request,
    brain_id: str = Depends(get_brain_id),
):
    result = await memory_store(
        text=body.text,
        metadata=body.metadata,
        brain_id=brain_id,
        request=request,
    )
    return {
        "workspace": request.state.workspace_slug,
        "status": "queued",
        "task_id": result["task_id"],
    }


@agent_router.get("/tasks/{task_id}")
async def agent_task(
    task_id: str,
    request: Request,
    brain_id: str = Depends(get_brain_id),
):
    result = await memory_task_status(task_id, brain_id)
    return {**result, "workspace": request.state.workspace_slug}


@agent_router.get("/capabilities")
async def agent_capabilities(request: Request):
    workspace = _workspace(request)
    root = _base(request)
    public_root = root.removesuffix(f"/brains/{workspace.slug}/agent")
    workspace_root = f"{public_root}/brains/{workspace.slug}"
    return {
        "workspace": {
            "slug": workspace.slug,
            "brain_id": workspace.brain_id,
            "display_name": workspace.display_name,
        },
        "capabilities": {
            "search": True,
            "context": True,
            "memory_write": True,
            "tasks": True,
            "mcp": True,
            "neighbors": True,
        },
        "endpoints": {
            "native_api": f"{workspace_root}/api",
            "native_openapi": f"{workspace_root}/api/openapi.json",
            "agent_api": root,
            "agent_openapi": f"{root}/openapi.json",
            "mcp": f"{workspace_root}/mcp",
        },
    }


@lru_cache(maxsize=1)
def _agent_schema_template() -> dict:
    routes = [route for route in agent_router.routes if route.path != f"{AGENT_PREFIX}/openapi.json"]
    schema = get_openapi(
        title="BrainAPI Agent Memory API",
        version=BRAIN_VERSION,
        description="Stable workspace-scoped memory API for external agents.",
        routes=routes,
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {}).update(
        {
            "BrainPAT": {"type": "apiKey", "in": "header", "name": "BrainPAT"},
            "BearerAuth": {"type": "http", "scheme": "bearer"},
        }
    )
    for item in schema.get("paths", {}).values():
        for operation in item.values():
            if isinstance(operation, dict) and "responses" in operation:
                operation["security"] = [{"BrainPAT": []}, {"BearerAuth": []}]
    # Advertise paths relative to the workspace-specific server URL.
    schema["paths"] = {
        path.removeprefix(AGENT_PREFIX): item
        for path, item in schema.get("paths", {}).items()
    }
    return schema


@agent_router.get("/openapi.json", include_in_schema=False)
async def agent_openapi(request: Request):
    schema = deepcopy(_agent_schema_template())
    schema["servers"] = [{"url": _base(request)}]
    return schema
