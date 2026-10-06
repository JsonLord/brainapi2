from __future__ import annotations

import inspect
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable, Optional

from src.adapters.cache import CacheAdapter
from src.adapters.data import DataAdapter
from src.adapters.embeddings import EmbeddingsAdapter, VectorStoreAdapter
from src.adapters.graph import GraphAdapter
from src.adapters.llm import LLMAdapter
from src.core.plugins.prompts import PromptRegistry, prompt_registry

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI
    from mcp.server import FastMCP
    from src.config import Config


class PluginAdapters:
    def __init__(
        self,
        cache: CacheAdapter,
        graph: GraphAdapter,
        data: DataAdapter,
        vector_store: VectorStoreAdapter,
        llm_small: LLMAdapter,
        llm_large: LLMAdapter,
        embeddings: EmbeddingsAdapter,
        embeddings_small: EmbeddingsAdapter,
    ):
        self.cache = cache
        self.graph = graph
        self.data = data
        self.vector_store = vector_store
        self.llm_small = llm_small
        self.llm_large = llm_large
        self.embeddings = embeddings
        self.embeddings_small = embeddings_small


class PluginContext:
    def __init__(
        self,
        adapters: PluginAdapters,
        prompts: PromptRegistry,
        config: "Config",
        app: Optional["FastAPI"] = None,
        mcp: Optional["FastMCP"] = None,
        workspace_mcp: Optional["FastMCP"] = None,
    ):
        self._app = app
        self.mcp = mcp
        self.workspace_mcp = workspace_mcp or mcp
        self.adapters = adapters
        self.prompts = prompts
        self.config = config
        self._routers: list[tuple["APIRouter", dict[str, Any]]] = []
        self._event_handlers: dict[str, list[Callable]] = {}
        self._search_retrievers: dict[str, Callable] = {}
        self._search_rerankers: dict[str, Callable] = {}
        self._workspace_route_paths: set[tuple[str, str]] = set()
        self._workspace_mcp_tools: set[str] = set()

    @classmethod
    def _build_adapters(cls) -> PluginAdapters:
        from src.core.instances import (
            cache_adapter,
            data_adapter,
            embeddings_adapter,
            embeddings_small_adapter,
            graph_adapter,
            llm_large_adapter,
            llm_small_adapter,
            vector_store_adapter,
        )

        return PluginAdapters(
            cache=cache_adapter,
            graph=graph_adapter,
            data=data_adapter,
            vector_store=vector_store_adapter,
            llm_small=llm_small_adapter,
            llm_large=llm_large_adapter,
            embeddings=embeddings_adapter,
            embeddings_small=embeddings_small_adapter,
        )

    @classmethod
    def from_app(cls, app: "FastAPI") -> "PluginContext":
        from src.config import config

        return cls(
            app=app,
            adapters=cls._build_adapters(),
            prompts=prompt_registry,
            config=config,
        )

    @classmethod
    def from_mcp(
        cls, mcp: "FastMCP", *, workspace_mcp: Optional["FastMCP"] = None
    ) -> "PluginContext":
        from src.config import config

        return cls(
            mcp=mcp,
            workspace_mcp=workspace_mcp,
            adapters=cls._build_adapters(),
            prompts=prompt_registry,
            config=config,
        )

    def include_router(
        self,
        router: "APIRouter",
        skip_pat: bool = False,
        skip_brain: bool = False,
        **kwargs: Any,
    ) -> None:
        if self._app is None:
            return
        self._app.include_router(router, **kwargs)
        self._routers.append((router, kwargs))

        prefix = kwargs.get("prefix", "") or getattr(router, "prefix", "")
        if prefix:
            if skip_pat:
                from src.services.api.middlewares.auth import BrainPATMiddleware
                BrainPATMiddleware.excluded_prefixes.add(prefix)
            if skip_brain:
                from src.services.api.middlewares.brains import BrainMiddleware
                BrainMiddleware.excluded_prefixes.add(prefix)

    def add_middleware(self, middleware_cls: type, **kwargs: Any) -> None:
        if self._app is None:
            return
        self._app.add_middleware(middleware_cls, **kwargs)

    def register_mcp_tool(self, fn: Callable, **kwargs: Any) -> None:
        if self.mcp is None:
            return
        self.mcp.tool(**kwargs)(fn)

    def register_workspace_route(
        self,
        router: "APIRouter",
        *,
        capability: str,
    ) -> None:
        """Explicitly expose a plugin router below the hosted workspace API."""
        if self._app is None:
            return
        from fastapi.routing import APIRoute
        from src.services.api.dependencies import get_brain_id

        if not capability or not capability.replace("-", "").isalnum():
            raise ValueError("Workspace plugin capability must be URL-safe")
        if "/system" in (getattr(router, "prefix", "") or ""):
            raise ValueError("System routes cannot be workspace-hosted")
        routes = [route for route in router.routes if isinstance(route, APIRoute)]
        if not routes:
            raise ValueError("Workspace plugin router has no API routes")
        for route in routes:
            dependencies = {
                dependency.call for dependency in route.dependant.dependencies
            }
            if get_brain_id not in dependencies:
                raise ValueError(
                    f"Workspace plugin route {route.path} must declare "
                    "brain_id: str = Depends(get_brain_id)"
                )
            for method in route.methods:
                full_path = (
                    f"/brains/{{workspace_slug}}/api/plugins/{capability}{route.path}"
                )
                key = (method, full_path)
                existing = {
                    (candidate_method, candidate.path)
                    for candidate in self._app.routes
                    if isinstance(candidate, APIRoute)
                    for candidate_method in candidate.methods
                }
                if key in existing or key in self._workspace_route_paths:
                    raise ValueError(f"Workspace plugin route collision: {method} {full_path}")
                self._workspace_route_paths.add(key)
        self._app.include_router(
            router,
            prefix=f"/brains/{{workspace_slug}}/api/plugins/{capability}",
            tags=[f"workspace-plugin:{capability}"],
        )

    def register_workspace_mcp_tool(
        self,
        fn: Callable,
        *,
        name: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Register an opt-in MCP tool with immutable workspace context injection."""
        if self.workspace_mcp is None:
            return
        from src.services.brain_context import brain_context_var

        tool_name = name or fn.__name__
        existing_tools = getattr(
            getattr(self.workspace_mcp, "_tool_manager", None), "_tools", {}
        )
        if tool_name in self._workspace_mcp_tools or tool_name in existing_tools:
            raise ValueError(f"Workspace MCP tool collision: {tool_name}")
        if "brain_context" not in inspect.signature(fn).parameters:
            raise ValueError(
                "Workspace MCP tools must declare a brain_context parameter"
            )

        @wraps(fn)
        async def scoped(*args: Any, **call_kwargs: Any):
            context = brain_context_var.get()
            if context is None:
                raise PermissionError("Workspace MCP context is required")
            call_kwargs["brain_context"] = context
            result = fn(*args, **call_kwargs)
            return await result if inspect.isawaitable(result) else result

        signature = inspect.signature(fn)
        scoped.__signature__ = signature.replace(  # type: ignore[attr-defined]
            parameters=[
                parameter
                for parameter in signature.parameters.values()
                if parameter.name != "brain_context"
            ]
        )
        self._workspace_mcp_tools.add(tool_name)
        self.workspace_mcp.tool(name=tool_name, **kwargs)(scoped)

    def add_event_handler(self, event: str, handler: Callable) -> None:
        self._event_handlers.setdefault(event, []).append(handler)

    def register_search_retriever(self, name: str, fn: Callable) -> None:
        from src.core.search.hooks import register_search_retriever

        register_search_retriever(name, fn)
        self._search_retrievers[name] = fn

    def register_search_reranker(self, name: str, fn: Callable) -> None:
        from src.core.search.hooks import register_search_reranker

        register_search_reranker(name, fn)
        self._search_rerankers[name] = fn
