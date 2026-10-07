"""
File: /brains.py
Created Date: Monday December 1st 2025
Author: Christian Nonis <alch.infoemail@gmail.com>
-----
Last Modified: Wednesday March 4th 2026 9:35:41 pm
Modified By: Christian Nonis <alch.infoemail@gmail.com>
-----
"""

import json
import os
import re
from email import policy
from email.parser import BytesParser

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette import status

from src.services.api.console_static import is_console_path
from src.services.api.errors import error_response
from src.services.data.main import data_adapter
from src.services.kg_agent.main import cache_adapter
from src.services.workspaces import WorkspaceScopeConflictError, resolve_workspace_scope


def _brain_id_from_multipart(body: bytes, content_type: str) -> str | None:
    try:
        message_bytes = (
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8", errors="replace")
            + body
        )
        msg = BytesParser(policy=policy.HTTP).parsebytes(message_bytes)
        for part in msg.iter_parts():
            if part.get_content_disposition() != "form-data":
                continue
            name = part.get_param("name", header="content-disposition")
            if name == "brain_id":
                raw = part.get_content_text()
                return raw.rstrip() if raw else None
    except Exception:
        pass
    return None


class BrainMiddleware(BaseHTTPMiddleware):
    excluded_prefixes: set[str] = {"/console", "/docs", "/redoc", "/demo"}
    brain_exempt_paths: set[str] = {
        "/health",
        "/openapi.json",
        "/meta/login-info",
    }

    async def dispatch(self, request: Request, call_next):
        if is_console_path(request.url.path):
            return await call_next(request)

        if any(request.url.path.startswith(p) for p in self.excluded_prefixes):
            return await call_next(request)

        if request.url.path in self.brain_exempt_paths:
            return await call_next(request)

        brainpat = request.headers.get("BrainPAT")
        authorization = request.headers.get("Authorization", "")
        if not brainpat and not authorization.lower().startswith("bearer "):
            # Authentication middleware owns the missing-credential response. Avoid
            # touching a backing store before it can return a structured 401.
            return await call_next(request)

        workspace_match = re.match(
            r"^/brains/(?P<slug>[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)/(?:api|agent|mcp)(?:/|$)",
            request.url.path,
        )

        async def _get_brain_id():
            header_brain_id = request.headers.get("X-Brain-ID")
            brain_id = header_brain_id

            if brain_id:
                brain_id = brain_id.rstrip()

            if brain_id is None:
                brain_id = request.query_params.get("brain_id")
                if brain_id:
                    brain_id = brain_id.rstrip()

            body_brain_id = None
            if (brain_id is None or workspace_match) and request.method in (
                "POST",
                "PUT",
                "PATCH",
            ):
                content_type = request.headers.get("content-type", "") or ""
                body = await request.body()
                if "application/json" in content_type and body:
                    try:
                        body_data = json.loads(body)
                        if isinstance(body_data, dict):
                            body_brain_id = body_data.get("brain_id")
                            if body_brain_id:
                                body_brain_id = body_brain_id.rstrip()
                    except (json.JSONDecodeError, ValueError):
                        pass
                elif "multipart/form-data" in content_type and body:
                    form_brain_id = _brain_id_from_multipart(body, content_type)
                    if form_brain_id:
                        body_brain_id = form_brain_id

                body_sent = [False]

                async def receive():
                    if not body_sent[0]:
                        body_sent[0] = True
                        return {"type": "http.request", "body": body, "more_body": False}
                    return {"type": "http.request", "body": b"", "more_body": False}

                request._receive = receive
            if brain_id is None and body_brain_id:
                brain_id = body_brain_id

            if workspace_match:
                slug = workspace_match.group("slug")
                try:
                    workspace = data_adapter.get_workspace(slug)
                except Exception:
                    return None, error_response(
                        request, status_code=503, detail="Workspace store unavailable"
                    )
                if workspace is None:
                    return None, error_response(
                        request, status_code=404, detail="Workspace not found"
                    )
                if workspace.archived:
                    return None, error_response(
                        request, status_code=410, detail="Workspace is archived"
                    )
                try:
                    brain_id = resolve_workspace_scope(
                        workspace,
                        header_brain_id=header_brain_id,
                        query_brain_id=request.query_params.get("brain_id"),
                        body_brain_id=body_brain_id,
                    )
                except WorkspaceScopeConflictError:
                    return None, error_response(
                        request,
                        status_code=409,
                        detail="Workspace URL conflicts with an explicit brain scope.",
                        code="BRAIN_SCOPE_CONFLICT",
                        message="The workspace URL is authoritative.",
                        resolution="Remove the conflicting X-Brain-ID or brain_id value.",
                    )
                request.state.workspace_slug = slug
            request.state.brain_id = brain_id
            return brain_id, None

        # Variables ----------------------------------------------
        brain_id, scope_error = await _get_brain_id()
        if scope_error is not None:
            return scope_error
        brain_creation_allowed = os.getenv("BRAIN_CREATION_ALLOWED") == "true"
        default_brain_fallback = os.getenv("DEFAULT_BRAIN_FALLBACK") == "true"

        # Bypassing system routes --------------------------------
        if request.url.path.startswith("/system") or request.url.path == "/":
            return await call_next(request)

        # Cleanup checks -----------------------------------------
        if brain_id == "system":
            return error_response(
                request,
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="System brain is not allowed to be used.",
                code="BRAIN_ID_RESERVED",
                message="The system brain is reserved.",
                resolution="Select a non-system application brain.",
            )
        if brain_id and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", brain_id
        ):
            return error_response(
                request,
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Brain ID contains unsupported characters.",
                code="BRAIN_ID_INVALID",
                message="The brain identifier is invalid.",
                resolution="Use letters, digits, hyphens, or underscores.",
                extra={"value": brain_id},
            )

        try:
            cached_brain_id = cache_adapter.get(
                key=f"brain:{brain_id}", brain_id="system"
            )
            if brain_id and not cached_brain_id:
                stored_brain = data_adapter.get_brain(name_key=brain_id)

                request.state.brain_id = brain_id

                if not stored_brain and brain_creation_allowed:
                    new_brain = data_adapter.create_brain(name_key=brain_id)
                    cache_adapter.set(
                        key=f"brain:{brain_id}",
                        value=new_brain.id,
                        brain_id="system",
                    )
                elif stored_brain:
                    cache_adapter.set(
                        key=f"brain:{brain_id}",
                        value=stored_brain.id,
                        brain_id="system",
                    )
            elif not brain_id and default_brain_fallback:
                default_brain = data_adapter.get_brain(name_key="default")
                if not default_brain:
                    default_brain = data_adapter.create_brain(name_key="default")
                    cache_adapter.set(
                        key="brain:default",
                        value=default_brain.id,
                        brain_id="system",
                    )
                    request.state.brain_id = "default"
                else:
                    cache_adapter.set(
                        key="brain:default",
                        value=default_brain.id,
                        brain_id="system",
                    )
                    request.state.brain_id = "default"
        except Exception:
            request.state.brain_id = brain_id or "default"

        if getattr(request.state, "brain_id", None) is None:
            return error_response(
                request,
                status_code=status.HTTP_406_NOT_ACCEPTABLE,
                detail="Brain not found or creation is not allowed.",
            )

        return await call_next(request)
