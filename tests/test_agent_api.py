import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.services.api.middlewares.auth import BrainPATMiddleware
from src.services.api.middlewares.brains import BrainMiddleware
from src.services.api.routes.agent import agent_router
from tests.test_workspace_facade import FacadeCache, FacadeData


def agent_client(monkeypatch):
    from src.services.api.middlewares import auth, brains
    from src.services.api.routes import agent

    data = FacadeData()
    cache = FacadeCache()
    monkeypatch.setenv("BRAINPAT_TOKEN", "system-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://brain.example")
    monkeypatch.setattr(brains, "data_adapter", data)
    monkeypatch.setattr(brains, "cache_adapter", cache)
    monkeypatch.setattr(auth, "data_adapter", data)
    monkeypatch.setattr(auth, "cache_adapter", cache)
    monkeypatch.setattr(agent, "data_adapter", data)

    calls = []

    async def search(query, limit, brain_id):
        calls.append(("search", brain_id, query))
        return [{"id": brain_id, "type": "chunk", "text": query}]

    async def context(query, max_tokens, brain_id):
        calls.append(("context", brain_id, query))
        return {"context": f"{brain_id}:{query}", "sources": []}

    async def store(**kwargs):
        calls.append(
            (
                "store",
                kwargs["brain_id"],
                kwargs["text"],
                kwargs["metadata"],
                kwargs["request"].headers.get("Idempotency-Key"),
            )
        )
        return {"task_id": f'{kwargs["brain_id"]}-task'}

    async def task(task_id, brain_id):
        if task_id != f"{brain_id}-task":
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Task not found")
        return {
            "task_id": task_id,
            "brain_id": brain_id,
            "status": "queued",
            "workspace": "untrusted-task-payload",
        }

    monkeypatch.setattr(agent, "memory_search", search)
    monkeypatch.setattr(agent, "memory_context", context)
    monkeypatch.setattr(agent, "memory_store", store)
    monkeypatch.setattr(agent, "memory_task_status", task)
    app = FastAPI()
    app.add_middleware(BrainPATMiddleware)
    app.add_middleware(BrainMiddleware)
    app.include_router(agent_router)
    return TestClient(app), calls


def test_agent_operations_use_canonical_workspace_scope(monkeypatch):
    client, calls = agent_client(monkeypatch)
    headers = {"Authorization": "Bearer system-secret"}

    assert client.post(
        "/brains/alpha/agent/search", headers=headers, json={"query": "memory"}
    ).json()["results"][0]["id"] == "alpha"
    assert client.post(
        "/brains/beta/agent/context", headers=headers, json={"query": "context"}
    ).json()["context"] == "beta:context"
    stored = client.post(
        "/brains/alpha/agent/memory",
        headers={**headers, "Idempotency-Key": "agent-message-1"},
        json={"text": "ALPHA_ONLY_FACT", "metadata": {"source": "hermes"}},
    )
    assert stored.status_code == 202
    assert stored.json() == {
        "workspace": "alpha",
        "status": "queued",
        "task_id": "alpha-task",
    }
    assert (
        "store",
        "alpha",
        "ALPHA_ONLY_FACT",
        {"source": "hermes"},
        "agent-message-1",
    ) in calls
    assert client.get(
        "/brains/beta/agent/tasks/alpha-task", headers=headers
    ).status_code == 404
    own_task = client.get("/brains/alpha/agent/tasks/alpha-task", headers=headers)
    assert own_task.json()["workspace"] == "alpha"


def test_agent_security_discovery_and_openapi(monkeypatch):
    client, _ = agent_client(monkeypatch)
    system = {"Authorization": "Bearer system-secret"}
    assert client.get(
        "/brains/beta/agent/capabilities",
        headers={"Authorization": "Bearer alpha-secret"},
    ).status_code == 403
    assert client.get(
        "/brains/beta/agent/capabilities",
        headers={"Authorization": "Bearer invalid-secret"},
    ).status_code == 401
    assert client.get("/brains/archived/agent/capabilities", headers=system).status_code == 410
    assert client.get("/brains/missing/agent/capabilities", headers=system).status_code == 404
    conflict = client.post(
        "/brains/alpha/agent/search",
        headers=system,
        json={"query": "x", "brain_id": "beta"},
    )
    assert conflict.status_code == 409

    capabilities = client.get(
        "/brains/alpha/agent/capabilities", headers=system
    ).json()
    assert capabilities["workspace"] == {
        "slug": "alpha",
        "brain_id": "alpha",
        "display_name": "Alpha",
    }
    assert capabilities["endpoints"]["agent_api"].endswith("/brains/alpha/agent")
    assert capabilities["endpoints"]["native_openapi"].endswith(
        "/brains/alpha/api/openapi.json"
    )
    assert capabilities["endpoints"]["agent_openapi"].endswith(
        "/brains/alpha/agent/openapi.json"
    )
    assert capabilities["endpoints"]["mcp"].endswith("/brains/alpha/mcp")
    schema = client.get("/brains/alpha/agent/openapi.json", headers=system).json()
    assert schema["servers"] == [{"url": "https://brain.example/brains/alpha/agent"}]
    assert set(schema["paths"]) == {
        "/search",
        "/context",
        "/memory",
        "/tasks/{task_id}",
        "/capabilities",
    }


def test_agent_metadata_survives_canonical_ingestion_and_chunk_retrieval(monkeypatch):
    import asyncio
    from unittest.mock import MagicMock, patch

    from fastapi.responses import JSONResponse

    from src.constants.embeddings import Vector
    from src.services import agent_memory
    from src.services.api.controllers import structured_data
    from src.workers.tasks import ingestion

    queued_payload = {}

    async def queue(body, request, brain_id):
        body.brain_id = brain_id
        queued_payload.update(body.model_dump())
        return JSONResponse({"message": "accepted", "task_id": "alpha-task"}, 202)

    monkeypatch.setattr(agent_memory, "ingest_data", queue)
    result = asyncio.run(
        agent_memory.memory_store(
            text="Customer prefers annual billing.",
            metadata={"source": "hermes", "session_id": "abc"},
            brain_id="alpha",
            request=type("Request", (), {"headers": {}})(),
        )
    )
    assert result["task_id"] == "alpha-task"
    assert queued_payload["brain_id"] == "alpha"
    assert queued_payload["meta_keys"] == {
        "source": "hermes",
        "session_id": "abc",
    }

    class MemoryData:
        def __init__(self):
            self.chunks = {}

        def save_text_chunk(self, chunk, brain_id):
            self.chunks.setdefault(brain_id, []).append(chunk)
            return chunk

        def get_text_chunks(
            self, brain_id, limit, skip, query_text, metadata_eq, order
        ):
            chunks = self.chunks.get(brain_id, [])
            if metadata_eq:
                chunks = [
                    chunk
                    for chunk in chunks
                    if all((chunk.metadata or {}).get(k) == v for k, v in metadata_eq.items())
                ]
            return chunks[skip : skip + limit], len(chunks)

    repository = MemoryData()
    request = MagicMock(id="alpha-task")
    with patch.object(ingestion, "data_adapter", repository), patch.object(
        ingestion, "embeddings_adapter"
    ) as embeddings, patch.object(
        ingestion, "vector_store_adapter"
    ), patch.object(
        ingestion.config, "pipeline_mode", "lightweight"
    ), patch.object(
        ingestion, "set_ingestion_task_status"
    ), patch.object(
        structured_data, "data_adapter", repository
    ):
        embeddings.embed_text.return_value = Vector(
            id="vector-1", embeddings=[0.1], metadata={}
        )
        queued_payload["skip_enrichment"] = True
        ingestion.ingest_data.run.__func__(
            type("Bound", (), {"request": request})(), queued_payload
        )
        chunks, total = asyncio.run(
            structured_data.get_text_chunks(
                "alpha", metadata_eq={"source": "hermes", "session_id": "abc"}
            )
        )

    assert total == 1
    assert chunks[0].metadata == {"source": "hermes", "session_id": "abc"}


def test_agent_memory_idempotency_key_reuses_task_identifier(monkeypatch):
    import asyncio

    from src.services import agent_memory
    from src.services.api.routes import ingest

    queued = []
    monkeypatch.setattr(
        ingest.ingest_data_task,
        "apply_async",
        lambda *, args, task_id: queued.append((args[0]["brain_id"], task_id)),
    )
    monkeypatch.setattr(ingest, "set_ingestion_task_status", lambda *args, **kwargs: {})
    request = type("Request", (), {"headers": {"Idempotency-Key": "message-42"}})()

    first = asyncio.run(
        agent_memory.memory_store(
            text="Customer prefers annual billing.",
            metadata={"source": "hermes"},
            brain_id="alpha",
            request=request,
        )
    )
    second = asyncio.run(
        agent_memory.memory_store(
            text="Customer prefers annual billing.",
            metadata={"source": "hermes"},
            brain_id="alpha",
            request=request,
        )
    )

    assert first["task_id"] == second["task_id"] == "message-42"
    assert queued == [("alpha", "message-42"), ("alpha", "message-42")]


def test_relationship_worker_rejects_missing_brain_scope():
    from src.workers.tasks import ingestion

    with pytest.raises(ValueError, match="requires brain_id"):
        ingestion.process_architect_relationships.run.__func__(
            type("Bound", (), {"request": type("Request", (), {"id": "task"})()})(),
            {"relationships": []},
        )
