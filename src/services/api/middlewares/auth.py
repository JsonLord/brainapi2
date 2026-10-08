"""
File: /auth.py
Created Date: Thursday November 27th 2025
Author: Christian Nonis <alch.infoemail@gmail.com>
-----
Last Modified: Thursday February 19th 2026 7:45:12 pm
Modified By: Christian Nonis <alch.infoemail@gmail.com>
-----
"""

import os
import secrets
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette import status

from src.services.api.console_static import is_console_path
from src.services.api.errors import error_response
from src.services.kg_agent.main import cache_adapter
from src.services.data.main import data_adapter
from src.services.workspaces import WorkspaceAccessDeniedError, authorize_brain_pat


class BrainPATMiddleware(BaseHTTPMiddleware):
    excluded_prefixes: set[str] = {"/console", "/docs", "/redoc", "/demo", "/api-docs"}
    auth_exempt_paths: set[str] = {
        "/",
        "/health",
        "/api-docs",
        "/openapi.json",
        "/meta/login-info",
        "/favicon.ico",
    }

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        if is_console_path(request.url.path):
            return await call_next(request)

        if any(request.url.path.startswith(p) for p in self.excluded_prefixes) or "/mcp" in request.url.path:
            return await call_next(request)

        if request.url.path in self.auth_exempt_paths:
            return await call_next(request)
        brainpat = request.headers.get("BrainPAT") or getattr(
            request.state, "pat", None
        )
        if not brainpat:
            brainpat = request.headers.get("Authorization")
            if brainpat:
                scheme, _, token = brainpat.partition(" ")
                brainpat = token.rstrip() if scheme.lower() == "bearer" else None
        if not brainpat:
            return error_response(
                request,
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing BrainPAT header",
            )
        system_pat = os.getenv("BRAINPAT_TOKEN")
        workspace_discovery = (
            request.method == "GET"
            and request.url.path.startswith("/system/workspaces")
        )
        if request.url.path.startswith("/system") or request.url.path == "/":
            if system_pat and secrets.compare_digest(brainpat, system_pat):
                request.state.is_system_pat = True
                return await call_next(request)
            if workspace_discovery:
                try:
                    stored_brain = data_adapter.get_brain_by_pat(brainpat)
                except Exception:
                    return error_response(
                        request,
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Authentication store unavailable",
                    )
                if stored_brain:
                    request.state.is_system_pat = False
                    request.state.brain_id = stored_brain.name_key
                    return await call_next(request)
            return error_response(
                request,
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing BrainPAT header",
            )

        brain_id = getattr(request.state, "brain_id", None)
        if not brain_id:
            return error_response(
                request,
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Brain ID is required.",
                code="BRAIN_ID_REQUIRED",
                message="A brain identifier is required.",
                resolution="Send X-Brain-ID with the intended alphanumeric brain name.",
            )
        cachepat_key = f"brainpat:{brain_id}"
        if system_pat and secrets.compare_digest(brainpat, system_pat):
            return await call_next(request)

        def invalid_pat_status() -> int:
            if not getattr(request.state, "workspace_slug", None):
                return status.HTTP_401_UNAUTHORIZED
            # A valid PAT for a different brain is authenticated but forbidden.
            # A token unknown to the registry remains an authentication failure.
            return (
                status.HTTP_403_FORBIDDEN
                if data_adapter.get_brain_by_pat(brainpat)
                else status.HTTP_401_UNAUTHORIZED
            )

        try:
            cached_brainpat = cache_adapter.get(key=cachepat_key, brain_id="system")

            # Logic --------------------------------------------------
            if not cached_brainpat:
                stored_brain = data_adapter.get_brain(name_key=brain_id)
                try:
                    authorize_brain_pat(
                        brainpat,
                        system_pat=system_pat,
                        brain_pat=stored_brain.pat if stored_brain else None,
                        workspace_scoped=bool(
                            getattr(request.state, "workspace_slug", None)
                        ),
                    )
                except (WorkspaceAccessDeniedError, ValueError):
                    return error_response(
                        request,
                        status_code=invalid_pat_status(),
                        detail="Invalid or missing BrainPAT header",
                    )
                cached_brainpat = stored_brain.pat
                cache_adapter.set(
                    key=cachepat_key, value=stored_brain.pat, brain_id="system"
                )
            else:
                try:
                    authorize_brain_pat(
                        brainpat,
                        system_pat=system_pat,
                        brain_pat=cached_brainpat,
                        workspace_scoped=bool(
                            getattr(request.state, "workspace_slug", None)
                        ),
                    )
                except (WorkspaceAccessDeniedError, ValueError):
                    return error_response(
                        request,
                        status_code=invalid_pat_status(),
                        detail="Invalid or missing BrainPAT header",
                    )
        except Exception:
            return error_response(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication store unavailable",
            )

        response = await call_next(request)

        return response
