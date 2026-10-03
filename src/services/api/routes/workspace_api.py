from fastapi import APIRouter, Depends

from src.services.api.constants.requests import GetContextRequestBody
from src.services.api.constants.responses import GetContextResponse
from src.services.api.dependencies import get_brain_id
from src.services.api.routes.retrieve import get_context

workspace_api_router = APIRouter(
    prefix="/brains/{workspace_slug}/api", tags=["workspace-api"]
)


@workspace_api_router.post("/retrieve/context", response_model=GetContextResponse)
async def workspace_get_context(
    request: GetContextRequestBody,
    brain_id: str = Depends(get_brain_id),
) -> GetContextResponse:
    """Run the existing context retrieval handler in URL-selected brain scope."""
    return await get_context(request, brain_id)
