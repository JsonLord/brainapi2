import logging
import os
import re
import json
from contextlib import asynccontextmanager
from pathlib import Path

import dotenv
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

_project_root = Path(__file__).resolve().parent.parent.parent.parent
dotenv.load_dotenv(_project_root / ".env")

from src.lib.tracing.middleware import TraceMiddleware  # noqa: E402
from src.lib.tracing.runtime import (  # noqa: E402
    start_runtime_monitoring,
    stop_runtime_monitoring,
)
from src.services.mcp.main import (  # noqa: E402
    auth_token_var,
    mcp,
    oauth_provider,
    workspace_mcp,
)
from src.services.brain_context import BrainContext, brain_context_var  # noqa: E402
from src.services.data.main import data_adapter  # noqa: E402
from src.services.mcp.utils import guard_brainpat  # noqa: E402

PLUGINS_DIR = Path(os.getenv("PLUGINS_DIR", str(_project_root / "plugins")))

logger = logging.getLogger("brainapi.plugins")


def _load_mcp_plugins():
    from src.core.plugins.context import PluginContext
    from src.core.plugins.loader import PluginLoader

    ctx = PluginContext.from_mcp(mcp, workspace_mcp=workspace_mcp)
    loader = PluginLoader(plugins_dir=PLUGINS_DIR, context=ctx)
    results = loader.load_all()
    _log_plugin_banner(loader, results)
    failed = sorted(name for name, loaded in results.items() if not loaded)
    default_policy = (
        "warn" if os.getenv("ENV", "production").lower() == "development" else "fail"
    )
    policy = os.getenv("PLUGIN_FAILURE_POLICY", default_policy).strip().lower()
    if policy not in {"fail", "warn"}:
        raise RuntimeError("PLUGIN_FAILURE_POLICY must be 'fail' or 'warn'")
    if failed and policy == "fail":
        raise RuntimeError(f"Required plugins failed to load: {', '.join(failed)}")


def _log_plugin_banner(loader, results: dict[str, bool]):
    loaded = loader.loaded_plugins
    total = len(results)
    ok = sum(1 for v in results.values() if v)
    failed = total - ok

    lines = [
        "",
        "\033[35m ╔══════════════════════════════════════════════════════╗\033[0m",
        "\033[35m ║\033[0m          \033[1;35m⚡  BrainAPI MCP Plugin System  ⚡\033[0m          \033[35m║\033[0m",
        "\033[35m ╠══════════════════════════════════════════════════════╣\033[0m",
    ]

    if total == 0:
        lines.append(
            "\033[35m ║\033[0m  \033[2mNo plugins installed\033[0m                                \033[35m║\033[0m"
        )
    else:
        for name, success in results.items():
            manifest = loaded.get(name)
            if success and manifest:
                ver = f"v{manifest.version}"
                status = "\033[32m✔ loaded\033[0m"
                label = f"{manifest.name} ({ver})"
            else:
                status = "\033[31m✘ failed\033[0m"
                label = name
            padded = f"  {status}  {label}"
            visible_len = len(f"  ✔ loaded  {label}")
            pad = 54 - visible_len
            lines.append(f"\033[35m ║\033[0m{padded}{' ' * max(pad, 1)}\033[35m║\033[0m")

    lines.append("\033[35m ╠══════════════════════════════════════════════════════╣\033[0m")
    summary_parts = [f"\033[1;32m{ok} loaded\033[0m"]
    if failed:
        summary_parts.append(f"\033[1;31m{failed} failed\033[0m")
    summary_text = f"  {' · '.join(summary_parts)}"
    visible_summary_len = len(f"  {ok} loaded" + (f" · {failed} failed" if failed else ""))
    summary_pad = 54 - visible_summary_len
    lines.append(f"\033[35m ║\033[0m{summary_text}{' ' * max(summary_pad, 1)}\033[35m║\033[0m")
    lines.append("\033[35m ╚══════════════════════════════════════════════════════╝\033[0m")
    lines.append("")

    print("\n".join(lines))


_load_mcp_plugins()

_mcp_app = mcp.streamable_http_app()
_workspace_mcp_app = workspace_mcp.streamable_http_app()


@asynccontextmanager
async def _lifespan(app):
    start_runtime_monitoring("brainapi-mcp")
    async with _mcp_app.router.lifespan_context(app):
        async with _workspace_mcp_app.router.lifespan_context(app):
            try:
                yield
            finally:
                stop_runtime_monitoring("brainapi-mcp")


class AuthContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        context_token = None
        auth_context_token = None

        async def reject(status_code: int, detail: str):
            nonlocal auth_context_token
            if auth_context_token is not None:
                auth_token_var.reset(auth_context_token)
                auth_context_token = None
            await _json_error(send, status_code, detail)

        if scope["type"] in ("http", "websocket"):
            raw_headers = list(scope.get("headers", []))
            headers = dict(raw_headers)
            token = None
            brainpat = headers.get(b"brainpat")
            if brainpat and b"authorization" not in headers:
                raw_headers.append((b"authorization", b"Bearer " + brainpat))
                scope = {**scope, "headers": raw_headers}
            if brainpat:
                token = brainpat.decode()
            else:
                raw = (headers.get(b"authorization") or b"").decode()
                bearer = None
                if raw.startswith("Bearer: "):
                    bearer = raw.removeprefix("Bearer: ").strip() or None
                    if bearer:
                        raw_headers = [
                            (name, value)
                            for name, value in raw_headers
                            if name.lower() != b"authorization"
                        ]
                        raw_headers.append((b"authorization", f"Bearer {bearer}".encode()))
                        scope = {**scope, "headers": raw_headers}
                elif raw.startswith("Bearer "):
                    bearer = raw.removeprefix("Bearer ").strip() or None
                if bearer:
                    if oauth_provider:
                        pat = oauth_provider.get_pat_for_access_token(bearer)
                        token = pat if pat else bearer
                    else:
                        token = bearer
            auth_context_token = auth_token_var.set(token)
            match = re.match(
                r"^/brains/(?P<slug>[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)/mcp(?:/|$)",
                scope.get("path", ""),
            )
            if match:
                workspace = data_adapter.get_workspace(match.group("slug"))
                if workspace is None:
                    return await reject(404, "Workspace not found")
                if workspace.archived:
                    return await reject(410, "Workspace is archived")
                access = guard_brainpat(token)
                if not access:
                    return await reject(401, "Invalid BrainPAT")
                if access is not True and access != workspace.brain_id:
                    return await reject(403, "PAT cannot access this workspace")
                context_token = brain_context_var.set(
                    BrainContext(
                        workspace_slug=workspace.slug,
                        brain_id=workspace.brain_id,
                        auth_type="system" if access is True else "brain",
                    )
                )
                suffix = scope["path"][match.end() :]
                scope = {**scope, "path": f"/workspace/mcp{suffix}"}
        try:
            await self.app(scope, receive, send)
        finally:
            if context_token is not None:
                brain_context_var.reset(context_token)
            if auth_context_token is not None:
                auth_token_var.reset(auth_context_token)


async def _json_error(send, status_code: int, detail: str):
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _health(_request):
    return JSONResponse({"status": "ok"}, status_code=200)


async def _mcp_info(_request):
    body = {
        "service": "brainapi-mcp",
        "streamable_http": True,
        "path": "/mcp",
    }
    if oauth_provider:
        body["oauth"] = True
        body["oauth_consent_path"] = "/mcp-oauth/consent"
    return JSONResponse(body, status_code=200)


_custom_routes = [
    Route("/", _health, methods=["GET"]),
    Route("/mcp", _mcp_info, methods=["GET"]),
    Route("/mcp/info", _mcp_info, methods=["GET"]),
]
app = Starlette(
    routes=_custom_routes
    + [Mount("/workspace", app=_workspace_mcp_app), Mount("/", app=_mcp_app)],
    middleware=[
        Middleware(TraceMiddleware, service_name="brainapi-mcp"),
        Middleware(AuthContextMiddleware),
    ],
    lifespan=_lifespan,
)
