"""Public connection instructions shared by browsers and automation."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

connection_docs_router = APIRouter()
CONNECTION_GUIDE = Path(__file__).resolve().parents[3] / "CONNECTION.md"


@connection_docs_router.api_route("/api-docs", methods=["GET", "HEAD"], include_in_schema=False)
async def connection_guide():
    return FileResponse(
        CONNECTION_GUIDE,
        media_type="text/plain; charset=utf-8",
        filename="CONNECTION.md",
        content_disposition_type="inline",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"},
    )
