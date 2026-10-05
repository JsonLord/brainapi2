"""Shared semantic agent-memory operations over existing BrainAPI handlers."""

from __future__ import annotations

from typing import Any

from src.services.api.constants.requests import GetContextRequestBody, IngestionRequestBody
from src.constants.tasks.ingestion import IngestionTaskTextArgs
from src.services.api.routes.ingest import ingest_data
from src.services.api.routes.retrieve import get_context, retrieve
from src.services.api.routes.tasks import get_task
from src.services.api.controllers.entities import get_entity_info
from src.services.api.controllers.retrieve import retrieve_neighbors


def _json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


async def memory_search(query: str, limit: int, brain_id: str) -> list[dict]:
    response = await retrieve(query, limit, "", brain_id)
    results: list[dict] = []
    for kind, values in (
        ("chunk", response.data),
        ("observation", response.observations),
        ("relationship", response.relationships),
    ):
        for value in values:
            item = _json(value)
            results.append(
                {
                    "id": str(item.get("id") or item.get("uuid") or ""),
                    "type": kind,
                    "text": item.get("text") or item.get("description") or "",
                    "score": item.get("score"),
                    "metadata": item.get("metadata") or {},
                    "data": item,
                }
            )
    return results[:limit]


async def memory_context(query: str, max_tokens: int, brain_id: str) -> dict:
    # Existing context retrieval is fact/passage bounded rather than token bounded.
    # Convert the stable agent hint into conservative existing limits.
    max_facts = max(1, min(100, max_tokens // 100))
    request = GetContextRequestBody(text=query, max_facts=max_facts)
    response = await get_context(request, brain_id)
    payload = _json(response)
    return {
        "context": payload.get("text_context", ""),
        "sources": payload.get("source_passages", []),
        "triples": payload.get("triples", []),
    }


async def memory_store(
    *,
    text: str,
    metadata: dict,
    brain_id: str,
    request,
) -> dict:
    body = IngestionRequestBody(
        data=IngestionTaskTextArgs(text_data=text),
        meta_keys=metadata or None,
    )
    response = await ingest_data(body, request, brain_id)
    payload = response.body.decode("utf-8")
    import json

    return json.loads(payload)


async def memory_task_status(task_id: str, brain_id: str) -> dict:
    return await get_task(task_id, brain_id)


async def memory_neighbors(entity_uuid: str, limit: int, brain_id: str) -> Any:
    """Reuse canonical graph-neighbor retrieval for semantic MCP access."""
    return await retrieve_neighbors(uuid=entity_uuid, limit=limit, brain_id=brain_id)


async def memory_entity(target: str, max_depth: int, brain_id: str) -> Any:
    """Reuse canonical entity information retrieval for semantic MCP access."""
    return await get_entity_info(target, target, max_depth, brain_id)
