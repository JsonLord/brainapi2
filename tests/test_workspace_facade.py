import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.constants.data import Brain, Workspace
from src.services.api.middlewares.auth import BrainPATMiddleware
from src.services.api.middlewares.brains import BrainMiddleware
from src.services.api.routes.workspace_api import workspace_api_router


class FacadeData:
    def __init__(self):
        self.brains = {
            "alpha": Brain(name_key="alpha", pat="alpha-secret"),
            "beta": Brain(name_key="beta", pat="beta-secret"),
        }
        self.workspaces = {
            "alpha": Workspace(brain_id="alpha", slug="alpha", display_name="Alpha"),
            "beta": Workspace(brain_id="beta", slug="beta", display_name="Beta"),
            "archived": Workspace(
                brain_id="alpha",
                slug="archived",
                display_name="Archived",
                archived=True,
            ),
        }

    def get_workspace(self, slug):
        return self.workspaces.get(slug)

    def get_brain(self, name_key=None, **_kwargs):
        return self.brains.get(name_key)

    def get_brain_by_pat(self, pat):
        return next((brain for brain in self.brains.values() if brain.pat == pat), None)


class FacadeCache:
    def __init__(self):
        self.tasks = {
            ("alpha", "alpha-task"): json.dumps(
                {
                    "status": "completed",
                    "result": "ALPHA_ONLY_FACT",
                    "brain_id": "alpha",
                }
            ),
            ("beta", "beta-task"): json.dumps(
                {
                    "status": "completed",
                    "result": "BETA_ONLY_FACT",
                    "brain_id": "beta",
                }
            ),
            # Simulate a corrupt/migrated record placed in the wrong Redis
            # namespace. The persisted scope must still prevent disclosure.
            ("beta", "misfiled-alpha-task"): json.dumps(
                {
                    "status": "completed",
                    "result": "ALPHA_MISFILED_FACT",
                    "brain_id": "alpha",
                }
            ),
        }

    def get(self, key, brain_id="default"):
        if brain_id == "system" and key.startswith("brain:"):
            return key.removeprefix("brain:")
        if brain_id == "system" and key.startswith("brainpat:"):
            target = key.removeprefix("brainpat:")
            return f"{target}-secret"
        return None

    def get_task(self, task_id, brain_id="default"):
        return self.tasks.get((brain_id, task_id))

    def get_task_keys(self, brain_id="default"):
        return [f"{brain_id}:task:{task}" for scope, task in self.tasks if scope == brain_id]


def facade_client(monkeypatch):
    from src.services.api.middlewares import auth, brains
    from src.services.api.routes import ingest, meta, tasks, workspace_api

    data = FacadeData()
    cache = FacadeCache()
    monkeypatch.setenv("BRAINPAT_TOKEN", "system-secret")
    monkeypatch.setattr(brains, "data_adapter", data)
    monkeypatch.setattr(brains, "cache_adapter", cache)
    monkeypatch.setattr(auth, "data_adapter", data)
    monkeypatch.setattr(auth, "cache_adapter", cache)
    monkeypatch.setattr(workspace_api, "data_adapter", data)
    monkeypatch.setattr(tasks, "cache_adapter", cache)

    async def labels(brain_id):
        return [f"{brain_id.upper()}_ONLY_FACT"]

    monkeypatch.setattr(workspace_api, "get_entities_labels_controller", labels)
    monkeypatch.setattr(meta, "get_entities_labels_controller", labels)
    app = FastAPI()
    app.add_middleware(BrainPATMiddleware)
    app.add_middleware(BrainMiddleware)
    app.include_router(meta.meta_router)
    app.include_router(workspace_api_router)
    return TestClient(app), ingest


def test_hosted_scope_authorization_and_failures(monkeypatch):
    client, _ = facade_client(monkeypatch)
    system = {"Authorization": "Bearer system-secret"}

    discovery = client.get("/brains/alpha/api", headers=system)
    assert discovery.status_code == 200
    assert discovery.json()["links"] == {
        "native_api": "http://testserver/brains/alpha/api",
        "native_openapi": "http://testserver/brains/alpha/api/openapi.json",
        "openapi": "http://testserver/brains/alpha/api/openapi.json",
        "console": "http://testserver/console/w/alpha/",
        "agent_api": "http://testserver/brains/alpha/agent",
        "agent_openapi": "http://testserver/brains/alpha/agent/openapi.json",
        "mcp": "http://testserver/brains/alpha/mcp",
    }
    assert client.get("/brains/beta/api", headers=system).status_code == 200
    assert client.get(
        "/brains/alpha/api", headers={"Authorization": "Bearer alpha-secret"}
    ).status_code == 200
    assert client.get(
        "/brains/beta/api", headers={"Authorization": "Bearer alpha-secret"}
    ).status_code == 403
    assert client.get(
        "/brains/beta/api", headers={"Authorization": "Bearer invalid-secret"}
    ).status_code == 401
    assert client.get(
        "/brains/alpha/api",
        headers={**system, "X-Brain-ID": "beta"},
    ).status_code == 409
    query_conflict = client.get(
        "/brains/alpha/api/meta/entity-labels?brain_id=beta", headers=system
    )
    assert query_conflict.status_code == 409
    assert query_conflict.json()["error"]["code"] == "BRAIN_SCOPE_CONFLICT"
    assert client.get("/brains/archived/api", headers=system).status_code == 410
    assert client.get("/brains/missing/api", headers=system).status_code == 404


def test_hosted_data_and_task_isolation(monkeypatch):
    client, _ = facade_client(monkeypatch)
    headers = {"Authorization": "Bearer system-secret"}

    alpha = client.get("/brains/alpha/api/meta/entity-labels", headers=headers)
    beta = client.get("/brains/beta/api/meta/entity-labels", headers=headers)
    assert alpha.json() == ["ALPHA_ONLY_FACT"]
    assert beta.json() == ["BETA_ONLY_FACT"]

    own_task = client.get("/brains/alpha/api/tasks/alpha-task", headers=headers)
    cross_task = client.get("/brains/beta/api/tasks/alpha-task", headers=headers)
    misfiled_task = client.get(
        "/brains/beta/api/tasks/misfiled-alpha-task", headers=headers
    )
    beta_tasks = client.get("/brains/beta/api/tasks/", headers=headers)
    assert own_task.status_code == 200
    assert own_task.json()["result"] == "ALPHA_ONLY_FACT"
    assert own_task.json()["brain_id"] == "alpha"
    assert cross_task.status_code == 404
    assert misfiled_task.status_code == 404
    assert beta_tasks.status_code == 200
    assert [task["id"] for task in beta_tasks.json()["tasks"]] == ["beta-task"]


def test_hosted_ingest_uses_canonical_body_scope(monkeypatch):
    client, ingest = facade_client(monkeypatch)
    headers = {"Authorization": "Bearer system-secret"}
    queued = []
    statuses = []
    monkeypatch.setattr(
        ingest.ingest_data_task,
        "apply_async",
        lambda *, args, task_id: queued.append((args[0], task_id)),
    )
    monkeypatch.setattr(
        ingest,
        "set_ingestion_task_status",
        lambda task_id, brain_id, status, **kwargs: statuses.append(
            (task_id, brain_id, status, kwargs)
        ),
    )

    response = client.post(
        "/brains/alpha/api/ingest/",
        headers=headers,
        json={"data": {"data_type": "text", "text_data": "ALPHA_ONLY_FACT"}},
    )
    assert response.status_code == 202
    assert queued[0][0]["brain_id"] == "alpha"
    assert statuses[0][1:3] == ("alpha", "queued")

    conflict = client.post(
        "/brains/alpha/api/ingest/",
        headers=headers,
        json={
            "brain_id": "beta",
            "data": {"data_type": "text", "text_data": "BETA_ONLY_FACT"},
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "BRAIN_SCOPE_CONFLICT"
    assert len(queued) == 1


def test_legacy_header_scope_remains_supported(monkeypatch):
    client, _ = facade_client(monkeypatch)
    response = client.get(
        "/meta/entity-labels",
        headers={
            "Authorization": "Bearer system-secret",
            "X-Brain-ID": "beta",
        },
    )
    assert response.status_code == 200
    assert response.json() == ["BETA_ONLY_FACT"]
