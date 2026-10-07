import os

from fastapi import APIRouter, HTTPException, Request

from src.services.api.constants.workspaces import (
    CreateWorkspaceRequest,
    UpdateWorkspaceRequest,
)
from src.services.data.main import data_adapter
from src.services.workspaces import (
    WorkspaceConflictError,
    WorkspaceError,
    WorkspaceNotFoundError,
    WorkspaceService,
    public_base_url,
    workspace_payload,
)

workspace_router = APIRouter(prefix="/system/workspaces", tags=["workspaces"])


def get_workspace_service() -> WorkspaceService:
    return WorkspaceService(data_adapter)


def _base_url(request: Request) -> str:
    return public_base_url(os.getenv("PUBLIC_BASE_URL"), str(request.base_url))


def _not_found(error: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(error))


@workspace_router.get("", response_model=dict)
async def list_workspaces(request: Request):
    service = get_workspace_service()
    workspaces = service.bootstrap()
    if not getattr(request.state, "is_system_pat", False):
        brain_id = getattr(request.state, "brain_id", None)
        workspaces = [item for item in workspaces if item.brain_id == brain_id]
    return {
        "workspaces": [
            workspace_payload(item, _base_url(request))
            for item in workspaces
        ]
    }


@workspace_router.post("", status_code=201)
async def create_workspace(body: CreateWorkspaceRequest, request: Request):
    try:
        workspace = get_workspace_service().create(**body.model_dump())
    except WorkspaceConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except WorkspaceError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return workspace_payload(workspace, _base_url(request))


@workspace_router.get("/{slug}")
async def get_workspace(slug: str, request: Request):
    try:
        workspace = get_workspace_service().get(slug, include_archived=True)
    except (WorkspaceNotFoundError, WorkspaceError) as error:
        raise _not_found(error) from error
    if (
        not getattr(request.state, "is_system_pat", False)
        and workspace.brain_id != getattr(request.state, "brain_id", None)
    ):
        raise HTTPException(status_code=403, detail="PAT cannot access this workspace")
    return workspace_payload(workspace, _base_url(request))


@workspace_router.patch("/{slug}")
async def update_workspace(slug: str, body: UpdateWorkspaceRequest, request: Request):
    try:
        workspace = get_workspace_service().update(
            slug, **body.model_dump(exclude_unset=True)
        )
    except (WorkspaceNotFoundError, WorkspaceError) as error:
        raise _not_found(error) from error
    return workspace_payload(workspace, _base_url(request))


@workspace_router.get("/{slug}/endpoint")
async def get_workspace_endpoint(slug: str, request: Request):
    try:
        workspace = get_workspace_service().get(slug)
    except (WorkspaceNotFoundError, WorkspaceError) as error:
        raise _not_found(error) from error
    payload = workspace_payload(workspace, _base_url(request))
    return {
        "workspace": workspace.slug,
        "brain_id": workspace.brain_id,
        "api_base_url": payload["api_base_url"],
        "console_url": payload["console_url"],
        "auth": {
            "accepted": [
                "Authorization: Bearer <PAT>",
                "BrainPAT: <PAT>",
            ]
        },
    }
