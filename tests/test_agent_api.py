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
