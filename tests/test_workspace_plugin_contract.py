import pytest
from fastapi import APIRouter, Depends, FastAPI

from src.core.plugins.context import PluginContext
from src.services.api.dependencies import get_brain_id


def context_for(app=None, mcp=None, workspace_mcp=None):
    return PluginContext(
        adapters=None,
        prompts=None,
        config=None,
        app=app,
        mcp=mcp,
        workspace_mcp=workspace_mcp,
    )


def test_workspace_plugin_route_requires_explicit_brain_scope():
    app = FastAPI()
    router = APIRouter()

    @router.get("/unsafe")
    async def unsafe():
        return {}

    with pytest.raises(ValueError, match="must declare"):
        context_for(app=app).register_workspace_route(router, capability="demo")
    assert not any("/plugins/demo" in route.path for route in app.routes)


def test_workspace_plugin_route_registration_and_collision():
    app = FastAPI()
    router = APIRouter()

    @router.get("/safe")
    async def safe(brain_id: str = Depends(get_brain_id)):
        return {"brain_id": brain_id}

    context = context_for(app=app)
    context.register_workspace_route(router, capability="demo")
    assert any(
        route.path == "/brains/{workspace_slug}/api/plugins/demo/safe"
        for route in app.routes
    )
    with pytest.raises(ValueError, match="collision"):
        context.register_workspace_route(router, capability="demo")


def test_workspace_plugin_route_receives_authoritative_brain(monkeypatch):
    from fastapi.testclient import TestClient
    from src.services.api.middlewares import auth, brains
    from src.services.api.middlewares.auth import BrainPATMiddleware
    from src.services.api.middlewares.brains import BrainMiddleware
    from tests.test_workspace_facade import FacadeCache, FacadeData

    app = FastAPI()
    router = APIRouter()

    @router.get("/scope")
    async def scope(brain_id: str = Depends(get_brain_id)):
        return {"brain_id": brain_id}

    context_for(app=app).register_workspace_route(router, capability="demo")
    data, cache = FacadeData(), FacadeCache()
    monkeypatch.setenv("BRAINPAT_TOKEN", "system-secret")
    monkeypatch.setattr(brains, "data_adapter", data)
    monkeypatch.setattr(brains, "cache_adapter", cache)
    monkeypatch.setattr(auth, "data_adapter", data)
    monkeypatch.setattr(auth, "cache_adapter", cache)
    app.add_middleware(BrainPATMiddleware)
    app.add_middleware(BrainMiddleware)
    client = TestClient(app)
    headers = {"Authorization": "Bearer system-secret"}
    path = "/brains/alpha/api/plugins/demo/scope"
    assert client.get(path, headers=headers).json() == {"brain_id": "alpha"}
    assert client.get(
        path, headers={**headers, "X-Brain-ID": "beta"}
    ).status_code == 409


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, name=None, **_kwargs):
        def decorate(fn):
            self.tools[name or fn.__name__] = fn
            return fn

        return decorate


def test_workspace_mcp_tool_requires_and_receives_context():
    from src.services.brain_context import BrainContext, brain_context_var

    mcp = FakeMCP()
    context = context_for(mcp=mcp)

    def unsafe():
        return None

    with pytest.raises(ValueError, match="brain_context"):
        context.register_workspace_mcp_tool(unsafe)

    async def safe(value: str, brain_context):
        return f"{brain_context.brain_id}:{value}"

    context.register_workspace_mcp_tool(safe, name="safe")
    import inspect

    assert "brain_context" not in inspect.signature(mcp.tools["safe"]).parameters
    token = brain_context_var.set(BrainContext("alpha", "alpha", "brain"))
    try:
        import asyncio

        assert asyncio.run(mcp.tools["safe"]("value")) == "alpha:value"
    finally:
        brain_context_var.reset(token)


def test_ordinary_mcp_plugin_is_not_exposed_to_workspace_registry():
    legacy_mcp = FakeMCP()
    workspace_mcp = FakeMCP()
    context = context_for(mcp=legacy_mcp, workspace_mcp=workspace_mcp)

    def ordinary():
        return "legacy"

    async def workspace_safe(brain_context):
        return brain_context.brain_id

    context.register_mcp_tool(ordinary)
    context.register_workspace_mcp_tool(workspace_safe)

    assert "ordinary" in legacy_mcp.tools
    assert "ordinary" not in workspace_mcp.tools
    assert "workspace_safe" in workspace_mcp.tools
    assert "workspace_safe" not in legacy_mcp.tools
